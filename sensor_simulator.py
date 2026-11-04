"""
STAGE 1: Sensor Simulator for Vertical Aeroponic Farming
========================================================
This script simulates physical environmental and root-zone sensors
in an aeroponics chamber without requiring real hardware.

Key Features:
1. Generates realistic telemetry:
   - Ambient Temperature (20 - 30 °C)
   - Chamber Humidity (60 - 90 %)
   - Nutrient Reservoir Water Level (0 - 100 %)
   - Nutrient Solution pH (5.5 - 6.5)
   - Electrical Conductivity / EC (1.2 - 2.4 mS/cm)
   - Root-Zone Temperature (18 - 24 °C)
2. Implements a physical feedback loop:
   - When the misting pump is ON, chamber humidity increases.
   - When the misting pump is OFF, chamber humidity gradually evaporates / decreases.
3. Listens on `aeroponics/fault_inject` to simulate 4 critical fault modes:
   - Stuck Sensor (frozen identical reading)
   - Out-of-Range Sensor (e.g. -50 °C temperature spike)
   - Sensor Offline (simulator stops sending packets for 15 seconds)
   - Pump Failure (pump is commanded ON, but mist nozzles clogged -> humidity drops)
"""

import json
import os
import random
import sys
import time
from datetime import datetime
import paho.mqtt.client as mqtt

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Load system configuration
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

BROKER = CONFIG["mqtt"]["broker"]
PORT = CONFIG["mqtt"]["port"]
TOPIC_SENSORS = CONFIG["mqtt"]["topics"]["sensors"]
TOPIC_PUMP_STATE = CONFIG["mqtt"]["topics"]["pump_state"]
TOPIC_FAULT_INJECT = CONFIG["mqtt"]["topics"]["fault_inject"]


class AeroponicSensorSimulator:
    def __init__(self):
        # Baseline physical state variables
        self.temperature = 24.5       # °C
        self.humidity = 72.0          # %
        self.water_level = 85.0       # %
        self.ph = 6.0                 # pH scale
        self.ec = 1.8                 # mS/cm
        self.root_temp = 20.5         # °C

        # Actuator feedback state (updated via MQTT from fog_node)
        self.pump_state = "OFF"       # "ON" or "OFF"

        # Active fault injection flags
        self.fault_mode = "NONE"      # "NONE", "STUCK", "OUT_OF_RANGE", "OFFLINE", "PUMP_FAILURE"
        self.stuck_target_sensor = "humidity"
        self.stuck_frozen_value = None
        self.offline_until = 0.0

        # MQTT Client initialization
        # Compatible with both paho-mqtt v1 and v2
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="AeroponicsSimulator")
        except AttributeError:
            self.client = mqtt.Client(client_id="AeroponicsSimulator")

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback triggered when simulator connects to MQTT broker."""
        if rc == 0:
            print(f"[Simulator] Connected to MQTT Broker at {BROKER}:{PORT}")
            # Subscribe to pump feedback and fault injection topics
            client.subscribe(TOPIC_PUMP_STATE)
            client.subscribe(TOPIC_FAULT_INJECT)
            print(f"[Simulator] Subscribed to '{TOPIC_PUMP_STATE}' and '{TOPIC_FAULT_INJECT}'")
        else:
            print(f"[Simulator] Connection failed with result code: {rc}")

    def on_message(self, client, userdata, msg):
        """Processes pump state updates and fault injection commands."""
        topic = msg.topic
        payload_raw = msg.payload.decode("utf-8").strip()

        # 1. Feedback Loop: Track Pump State
        if topic == TOPIC_PUMP_STATE:
            try:
                data = json.loads(payload_raw)
                self.pump_state = data.get("state", "OFF").upper()
            except json.JSONDecodeError:
                self.pump_state = payload_raw.upper()
            print(f"[Simulator Feedback] Misting Pump is now: {self.pump_state}")

        # 2. Fault Injection Commands
        elif topic == TOPIC_FAULT_INJECT:
            print(f"[Simulator Fault Injection] Received command: {payload_raw}")
            cmd = payload_raw.upper()

            # Handle JSON or plain string
            try:
                parsed = json.loads(payload_raw)
                cmd = parsed.get("fault", cmd).upper()
            except Exception:
                pass

            if "STUCK" in cmd:
                self.fault_mode = "STUCK"
                self.stuck_frozen_value = self.humidity
                print(f"[Fault Mode Active] STUCK SENSOR: Humidity locked at {self.stuck_frozen_value:.1f}%")

            elif "OUT_OF_RANGE" in cmd or "RANGE" in cmd:
                self.fault_mode = "OUT_OF_RANGE"
                print("[Fault Mode Active] OUT-OF-RANGE: Temperature will spike to -50.0 °C")

            elif "OFFLINE" in cmd:
                self.fault_mode = "OFFLINE"
                self.offline_until = time.time() + 15.0
                print("[Fault Mode Active] SENSOR OFFLINE: Transmission paused for 15 seconds")

            elif "PUMP_FAILURE" in cmd:
                self.fault_mode = "PUMP_FAILURE"
                print("[Fault Mode Active] PUMP FAILURE: Pump ON but nozzles dry; humidity will drop")

            elif "CLEAR" in cmd or "RESET" in cmd or "NORMAL" in cmd:
                self.fault_mode = "NONE"
                self.stuck_frozen_value = None
                self.offline_until = 0.0
                print("[Fault Mode Cleared] Sensor simulator returned to normal baseline operation.")

    def update_physics(self):
        """
        Updates physical simulation state with realistic drift and actuator dynamics.
        """
        # Temperature natural drift (20 - 30 °C)
        self.temperature += random.uniform(-0.15, 0.15)
        self.temperature = max(20.0, min(30.0, self.temperature))

        # Root temperature tracks ambient with a damping thermal lag (18 - 24 °C)
        target_root = self.temperature * 0.75 + 3.0
        self.root_temp += (target_root - self.root_temp) * 0.05 + random.uniform(-0.05, 0.05)
        self.root_temp = max(18.0, min(24.0, self.root_temp))

        # Water level slowly depletes over time (0.01% per step)
        self.water_level -= random.uniform(0.01, 0.03)
        if self.water_level < 15.0:
            self.water_level = 95.0  # Automatic reservoir refill

        # pH drift (5.5 - 6.5)
        self.ph += random.uniform(-0.02, 0.02)
        self.ph = max(5.5, min(6.5, self.ph))

        # EC drift (1.2 - 2.4 mS/cm)
        self.ec += random.uniform(-0.01, 0.01)
        self.ec = max(1.2, min(2.4, self.ec))

        # --- Chamber Humidity Dynamics ---
        if self.fault_mode == "PUMP_FAILURE":
            # In pump failure, even if pump is ON, water spray fails so humidity drops
            self.humidity -= random.uniform(0.6, 1.2)
        elif self.pump_state == "ON":
            # Active misting: fine water droplets saturate the chamber
            self.humidity += random.uniform(2.0, 3.5)
        else:
            # Idle pump: natural evaporation and ventilation lower chamber humidity
            self.humidity -= random.uniform(0.4, 0.9)

        # Clamp humidity to realistic physical limits (40% to 98%)
        self.humidity = max(40.0, min(98.0, self.humidity))

    def generate_readings(self):
        """Constructs telemetry payload applying any active fault modes."""
        self.update_physics()

        temp_val = round(self.temperature, 2)
        hum_val = round(self.humidity, 2)
        water_val = round(self.water_level, 2)
        ph_val = round(self.ph, 2)
        ec_val = round(self.ec, 2)
        root_val = round(self.root_temp, 2)

        # Apply Fault Modes if active
        if self.fault_mode == "STUCK":
            hum_val = round(self.stuck_frozen_value if self.stuck_frozen_value else 71.55, 2)

        elif self.fault_mode == "OUT_OF_RANGE":
            # Sensor defect or line short: extreme abnormal value
            temp_val = -50.0

        payload = {
            "timestamp": datetime.now().isoformat(),
            "temperature": temp_val,
            "humidity": hum_val,
            "water_level": water_val,
            "ph": ph_val,
            "ec": ec_val,
            "root_temp": root_val
        }
        return payload

    def start(self):
        """Main loop: connects and publishes sensor data every 2 seconds."""
        print(f"[Simulator] Starting connection to {BROKER}:{PORT}...")
        try:
            self.client.connect(BROKER, PORT, 60)
        except Exception as e:
            print(f"[Simulator Error] Could not connect to MQTT Broker: {e}")
            print("Ensure Mosquitto broker or fallback broker is running on port 1883.")
            return

        self.client.loop_start()

        print("[Simulator] Publication loop active. Publishing every 2 seconds. Press Ctrl+C to stop.")
        try:
            while True:
                # Check if Sensor Offline fault is currently active
                now = time.time()
                if self.fault_mode == "OFFLINE":
                    if now < self.offline_until:
                        remaining = int(self.offline_until - now)
                        print(f"[Simulator OFFLINE FAULT] Suppressed telemetry ({remaining}s remaining)...")
                        time.sleep(2)
                        continue
                    else:
                        print("[Simulator OFFLINE FAULT] Offline window ended. Resuming telemetry...")
                        self.fault_mode = "NONE"

                # Generate and publish reading
                data = self.generate_readings()
                json_str = json.dumps(data)
                self.client.publish(TOPIC_SENSORS, json_str)
                print(f"[Simulator -> MQTT] Temp: {data['temperature']}°C | Hum: {data['humidity']}% | "
                      f"Root: {data['root_temp']}°C | Pump: {self.pump_state} | Fault: {self.fault_mode}")

                time.sleep(2.0)
        except KeyboardInterrupt:
            print("\n[Simulator] Stopping sensor simulator...")
        finally:
            self.client.loop_stop()
            self.client.disconnect()
            print("[Simulator] Disconnected cleanly.")


if __name__ == "__main__":
    simulator = AeroponicSensorSimulator()
    simulator.start()
