"""
STAGE 2 & 3: Fog Computing Node for Vertical Aeroponic Farming
==============================================================
The Fog Node acts as the intelligent on-site controller positioned
between the aeroponic sensor field and the cloud / dashboard.

Key Responsibilities:
1. Subscribes to telemetry from `aeroponics/sensors`.
2. Implements localized Smart Misting Control (AUTO / MANUAL modes):
   - Closed-loop threshold: Starts misting when Humidity < 70%, stops when Humidity > 85%.
   - Timed Duty Cycle: Actuates pump for 5 seconds, followed by rest interval.
   - Thermal Adaptation: Shortens rest interval when temperature exceeds 26°C.
3. Implements 4-tier Real-time Fault Detection:
   - Out-of-Range Sensor Anomaly (e.g. -50°C, pH out of bounds)
   - Stuck Sensor Anomaly (identical reading for N consecutive cycles)
   - Sensor Offline Anomaly (no telemetry received for > 10 seconds)
   - Pump Failure Anomaly (pump ON > 20s with no humidity increase)
   - Alert Cooldown: Prevents notification floods by enforcing cooldown windows.
4. Stage 6: Edge/Fog vs Cloud Latency Evaluation & Offline Autonomy:
   - Evaluates decision latency (local sub-millisecond vs cloud ~300ms).
   - In case of "Internet Down", local fog controller continues unhindered.
5. Stage 7: Telegram Bot Alerts (optional dispatch for critical anomalies).
6. Persists all readings, actuator events, and faults into local SQLite.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional
import paho.mqtt.client as mqtt
import requests

import database

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Load system configuration
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


CONFIG = load_config()

BROKER = CONFIG["mqtt"]["broker"]
PORT = CONFIG["mqtt"]["port"]
TOPIC_SENSORS = CONFIG["mqtt"]["topics"]["sensors"]
TOPIC_PUMP_STATE = CONFIG["mqtt"]["topics"]["pump_state"]
TOPIC_PUMP_COMMAND = CONFIG["mqtt"]["topics"]["pump_command"]
TOPIC_FAULTS = CONFIG["mqtt"]["topics"]["faults"]
TOPIC_SYSTEM_MODE = CONFIG["mqtt"]["topics"].get("system_mode", "aeroponics/system/mode")


class FogComputingNode:
    def __init__(self):
        # Database initialization
        database.init_db()

        # Operational state
        self.mode = "AUTO"            # "AUTO" or "MANUAL"
        self.pump_state = "OFF"       # "ON" or "OFF"
        self.last_sensor_data: Optional[Dict[str, Any]] = None
        self.last_sensor_time = time.time()

        # Misting Cycle Management
        self.cycle_state = "REST"     # "MISTING" or "REST"
        self.cycle_timer_start = time.time()
        self.pump_turned_on_time = 0.0
        self.humidity_at_pump_on = 0.0

        # Fault Detection Tracking State
        self.previous_readings = {
            "temperature": [],
            "humidity": [],
            "water_level": [],
            "ph": [],
            "ec": [],
            "root_temp": []
        }
        # Alert cooldown tracking: { "fault_key": timestamp_last_alerted }
        self.alert_cooldowns: Dict[str, float] = {}

        # Cloud Simulation & Resilience Toggles
        self.simulate_cloud = False
        self.internet_connected = True

        # MQTT Client setup
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="AeroponicsFogNode")
        except AttributeError:
            self.client = mqtt.Client(client_id="AeroponicsFogNode")

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Watchdog thread for detecting offline sensors
        self.running = True
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)

    def on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback when Fog Node connects to Mosquitto."""
        if rc == 0:
            print(f"[Fog Node] Connected to MQTT Broker ({BROKER}:{PORT})")
            client.subscribe(TOPIC_SENSORS)
            client.subscribe(TOPIC_PUMP_COMMAND)
            client.subscribe(TOPIC_SYSTEM_MODE)
            print(f"[Fog Node] Subscribed to '{TOPIC_SENSORS}', '{TOPIC_PUMP_COMMAND}', and '{TOPIC_SYSTEM_MODE}'")
            # Publish initial pump state
            self.publish_pump_state("OFF", "System Initialized")
        else:
            print(f"[Fog Node] MQTT connection failed with code: {rc}")

    def on_message(self, client, userdata, msg):
        """Handles inbound MQTT messages."""
        topic = msg.topic
        payload_str = msg.payload.decode("utf-8").strip()

        # Reload dynamic config parameters
        cfg = load_config()

        # 1. Incoming Sensor Telemetry
        if topic == TOPIC_SENSORS:
            try:
                data = json.loads(payload_str)
                self.process_sensor_data(data, cfg)
            except Exception as e:
                print(f"[Fog Node Error] Failed parsing sensor JSON: {e}")

        # 2. Manual Pump Command from Dashboard
        elif topic == TOPIC_PUMP_COMMAND:
            self.handle_pump_command(payload_str, cfg)

        # 3. Cloud / Network Simulation Mode Toggle
        elif topic == TOPIC_SYSTEM_MODE:
            try:
                mode_cfg = json.loads(payload_str)
                if "simulate_cloud" in mode_cfg:
                    self.simulate_cloud = bool(mode_cfg["simulate_cloud"])
                if "internet_connected" in mode_cfg:
                    self.internet_connected = bool(mode_cfg["internet_connected"])
                print(f"[Fog Node Mode Updated] Cloud Sim: {self.simulate_cloud} | Internet: {self.internet_connected}")
            except Exception as e:
                print(f"[Fog Node] Mode parse error: {e}")

    # =========================================================================
    # STAGE 2: CONTROL LOGIC (Smart Misting & Manual Override)
    # =========================================================================

    def handle_pump_command(self, command: str, cfg: Dict[str, Any]):
        """Processes manual or supervisory commands from dashboard."""
        cmd = command.strip().upper()
        print(f"[Fog Node] Received Actuator Command: '{cmd}'")

        if cmd == "AUTO":
            self.mode = "AUTO"
            self.cycle_state = "REST"
            self.cycle_timer_start = time.time()
            self.publish_pump_state("OFF", "Switched to AUTO Mode")
            print("[Fog Node] Control Mode set to AUTO.")

        elif cmd == "ON":
            self.mode = "MANUAL"
            self.execute_pump_decision("ON", "Manual Dashboard Override", cfg)
            print("[Fog Node] Control Mode set to MANUAL -> Pump ON.")

        elif cmd == "OFF":
            self.mode = "MANUAL"
            self.execute_pump_decision("OFF", "Manual Dashboard Override", cfg)
            print("[Fog Node] Control Mode set to MANUAL -> Pump OFF.")

    def evaluate_auto_misting(self, sensor_data: Dict[str, Any], cfg: Dict[str, Any]):
        """
        Executes localized smart misting control algorithm:
        1. Checks humidity threshold boundary:
           - Below humidity_low_threshold (default 70%): starts mist cycle
           - Above humidity_high_threshold (default 85%): stops mist cycle
        2. Applies timed duty cycle:
           - Mist for mist_duration_sec (e.g. 5 sec)
           - Rest for normal_rest_sec (e.g. 20 sec)
        3. Adapts rest duration based on temperature:
           - If temperature > high_temp_threshold (26°C), shorten rest to hot_rest_sec (8 sec)
        """
        if self.mode != "AUTO":
            return

        humidity = sensor_data.get("humidity", 75.0)
        temp = sensor_data.get("temperature", 24.0)

        m_cfg = cfg["misting_control"]
        low_hum = m_cfg.get("humidity_low_threshold", 70.0)
        high_hum = m_cfg.get("humidity_high_threshold", 85.0)
        high_temp = m_cfg.get("high_temp_threshold", 26.0)
        mist_dur = m_cfg.get("mist_duration_sec", 5.0)
        rest_dur = m_cfg.get("hot_rest_sec", 8.0) if temp >= high_temp else m_cfg.get("normal_rest_sec", 20.0)

        now = time.time()
        elapsed = now - self.cycle_timer_start

        # Humidity emergency override
        if humidity > high_hum and self.pump_state == "ON":
            self.cycle_state = "REST"
            self.cycle_timer_start = now
            self.execute_pump_decision("OFF", f"High Humidity Cutoff ({humidity:.1f}% > {high_hum}%)", cfg)
            return

        # State Machine: MISTING vs REST
        if self.cycle_state == "REST":
            # Start misting if humidity is below low_threshold OR rest interval has elapsed
            if humidity < low_hum or elapsed >= rest_dur:
                self.cycle_state = "MISTING"
                self.cycle_timer_start = now
                reason = f"Low Humidity ({humidity:.1f}%)" if humidity < low_hum else f"Periodic Cycle ({rest_dur}s rest elapsed)"
                self.execute_pump_decision("ON", reason, cfg)

        elif self.cycle_state == "MISTING":
            # Stop misting once mist duration is reached
            if elapsed >= mist_dur:
                self.cycle_state = "REST"
                self.cycle_timer_start = now
                self.execute_pump_decision("OFF", f"Mist Cycle Finished ({mist_dur}s duration)", cfg)

    def execute_pump_decision(self, new_state: str, reason: str, cfg: Dict[str, Any]):
        """
        Enacts the pump decision, evaluating Fog vs Cloud latency and resilience.
        """
        start_time = time.time()
        architecture = "FOG"
        status = "SUCCESS"

        if self.simulate_cloud:
            architecture = "CLOUD"
            if not self.internet_connected:
                # Cloud path fails due to network outage!
                print("[RESILIENCE TEST] Cloud unreachable (Internet Down)! Fog Node falling back locally.")
                status = "FAILED (CLOUD TIMEOUT)"
                database.log_latency("CLOUD", 1500.0, "FAILED")
                # Autonomous Fog fallback executes with zero network delay
                architecture = "FOG (FALLBACK)"
                status = "SUCCESS (LOCAL RESILIENCE)"
            else:
                # Simulate Cloud network round-trip delay (~300ms)
                delay_sec = cfg.get("cloud_simulation", {}).get("network_delay_ms", 300) / 1000.0
                time.sleep(delay_sec)

        decision_latency_ms = round((time.time() - start_time) * 1000, 2)
        database.log_latency(architecture, decision_latency_ms, status)

        if self.pump_state != new_state:
            self.pump_state = new_state
            if new_state == "ON":
                self.pump_turned_on_time = time.time()
                if self.last_sensor_data:
                    self.humidity_at_pump_on = self.last_sensor_data.get("humidity", 70.0)

            self.publish_pump_state(new_state, reason, decision_latency_ms)
            database.log_pump_event(new_state, self.mode, reason, decision_latency_ms)

    def publish_pump_state(self, state: str, reason: str = "", latency_ms: float = 0.0):
        """Broadcasts pump state on MQTT."""
        payload = {
            "timestamp": datetime.now().isoformat(),
            "state": state,
            "mode": self.mode,
            "reason": reason,
            "latency_ms": latency_ms
        }
        self.client.publish(TOPIC_PUMP_STATE, json.dumps(payload), retain=True)
        print(f"[Fog Node -> MQTT] Pump {state} | Mode: {self.mode} | Reason: {reason} | Latency: {latency_ms}ms")

    # =========================================================================
    # STAGE 3: FAULT DETECTION LOGIC
    # =========================================================================

    def check_faults(self, data: Dict[str, Any], cfg: Dict[str, Any]):
        """
        Executes multi-parameter anomaly and fault detection.
        """
        f_cfg = cfg["fault_detection"]
        stuck_thresh = f_cfg.get("stuck_reading_threshold", 5)
        pump_fail_thresh = f_cfg.get("pump_failure_timeout_sec", 20.0)
        thresholds = cfg.get("sensor_thresholds", {})

        # 1. Out-of-Range Anomaly Detection
        for sensor, limits in thresholds.items():
            val = data.get(sensor)
            if val is not None:
                min_val = limits["min"]
                max_val = limits["max"]
                unit = limits.get("unit", "")
                if val < min_val or val > max_val:
                    msg = f"{sensor.replace('_', ' ').title()} value {val}{unit} is out of safe range [{min_val}, {max_val}{unit}]!"
                    self.raise_fault("OUT_OF_RANGE", sensor, "CRITICAL" if val < (min_val - 5) else "WARNING", msg, cfg)

        # 2. Stuck Sensor Anomaly Detection
        for sensor in self.previous_readings.keys():
            val = data.get(sensor)
            if val is not None:
                history = self.previous_readings[sensor]
                history.append(val)
                if len(history) > stuck_thresh:
                    history.pop(0)

                if len(history) == stuck_thresh and len(set(history)) == 1:
                    msg = f"Sensor '{sensor}' appears STUCK: identical value ({val}) for {stuck_thresh} consecutive cycles."
                    self.raise_fault("STUCK_SENSOR", sensor, "WARNING", msg, cfg)

        # 3. Pump Failure Anomaly Detection
        if self.pump_state == "ON":
            time_on = time.time() - self.pump_turned_on_time
            current_humidity = data.get("humidity", 70.0)
            # If pump has been running > 20s but humidity hasn't risen by at least 1.0%
            if time_on > pump_fail_thresh and (current_humidity <= self.humidity_at_pump_on + 0.5):
                msg = f"Misting Pump Failure Detected: Pump active for {time_on:.1f}s, but humidity failed to increase (started at {self.humidity_at_pump_on}%, currently {current_humidity}%)."
                self.raise_fault("PUMP_FAILURE", "pump", "CRITICAL", msg, cfg)

    def raise_fault(self, fault_type: str, sensor_name: str, severity: str, message: str, cfg: Dict[str, Any]):
        """Dispatches fault alerts with cooldown protection to avoid spamming."""
        key = f"{fault_type}_{sensor_name}"
        now = time.time()
        cooldown = cfg.get("fault_detection", {}).get("fault_cooldown_sec", 10.0)

        # Check cooldown
        if key in self.alert_cooldowns:
            if now - self.alert_cooldowns[key] < cooldown:
                return  # In cooldown; do not spam duplicate alert

        self.alert_cooldowns[key] = now

        fault_payload = {
            "timestamp": datetime.now().isoformat(),
            "fault_type": fault_type,
            "sensor_name": sensor_name,
            "severity": severity,
            "message": message
        }

        # 1. Publish to MQTT
        self.client.publish(TOPIC_FAULTS, json.dumps(fault_payload))
        print(f"[Fog Alert -> MQTT] [{severity}] {fault_type}: {message}")

        # 2. Persist in local SQLite
        database.log_fault(fault_type, sensor_name, severity, message)

        # 3. Stage 7: Telegram dispatch (optional)
        self.send_telegram_alert(fault_payload, cfg)

    def _watchdog_loop(self):
        """Background thread checking if sensor node goes offline."""
        while self.running:
            time.sleep(2.0)
            cfg = load_config()
            offline_limit = cfg.get("fault_detection", {}).get("sensor_offline_timeout_sec", 10.0)
            now = time.time()

            if self.last_sensor_data is not None:
                elapsed = now - self.last_sensor_time
                if elapsed > offline_limit:
                    msg = f"Sensor Node OFFLINE! No telemetry received for {elapsed:.1f} seconds (limit: {offline_limit}s)."
                    self.raise_fault("SENSOR_OFFLINE", "gateway", "CRITICAL", msg, cfg)

    # =========================================================================
    # STAGE 7: TELEGRAM NOTIFICATIONS
    # =========================================================================

    def send_telegram_alert(self, fault_data: Dict[str, Any], cfg: Dict[str, Any]):
        """
        Sends Telegram alert if configured. Skips silently if credentials are blank.
        """
        tg_cfg = cfg.get("telegram", {})
        token = tg_cfg.get("bot_token", "").strip()
        chat_id = tg_cfg.get("chat_id", "").strip()

        if not token or not chat_id:
            return  # Telegram not configured; skip silently without error

        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            text = (
                f"🚨 *AEROPONICS FOG ALERT*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *Severity:* {fault_data['severity']}\n"
                f"• *Type:* {fault_data['fault_type']}\n"
                f"• *Target:* {fault_data['sensor_name']}\n"
                f"• *Time:* {fault_data['timestamp']}\n"
                f"• *Details:* {fault_data['message']}"
            )
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            requests.post(url, json=payload, timeout=3.0)
            print(f"[Telegram] Dispatched alert to Chat ID: {chat_id}")
        except Exception as e:
            print(f"[Telegram Notice] Could not deliver Telegram alert: {e}")

    # =========================================================================
    # MAIN INTAKE LOOP
    # =========================================================================

    def process_sensor_data(self, data: Dict[str, Any], cfg: Dict[str, Any]):
        """Main pipeline triggered whenever a sensor packet arrives."""
        self.last_sensor_data = data
        self.last_sensor_time = time.time()

        # 1. Log reading into SQLite
        database.log_reading(data)

        # 2. Run Fault Detection Pipeline
        self.check_faults(data, cfg)

        # 3. Evaluate Closed-Loop Misting Actuation
        self.evaluate_auto_misting(data, cfg)

    def start(self):
        """Starts Fog Node processing."""
        print(f"[Fog Node] Connecting to MQTT Broker at {BROKER}:{PORT}...")
        try:
            self.client.connect(BROKER, PORT, 60)
        except Exception as e:
            print(f"[Fog Node Error] Broker connection failed: {e}")
            print("Please ensure Mosquitto or fallback broker is active.")
            return

        self.client.loop_start()
        self.watchdog_thread.start()
        print("[Fog Node] Running. Monitoring sensors and controlling misting. Press Ctrl+C to stop.")

        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[Fog Node] Shutting down...")
        finally:
            self.running = False
            self.client.loop_stop()
            self.client.disconnect()
            print("[Fog Node] Exited cleanly.")


if __name__ == "__main__":
    node = FogComputingNode()
    node.start()
