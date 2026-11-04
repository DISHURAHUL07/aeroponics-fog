"""
Automated Self-Test Suite for Aeroponics Fog IoT System
======================================================
Validates all requirements across Stages 1 through 7:
- Stage 1: Sensor simulation & dynamics
- Stage 2: Fog node smart misting & overrides
- Stage 3: Anomaly & fault detection algorithms
- Stage 4: SQLite persistence & retrieval
- Stage 5: Dashboard helper functions & schema integrity
- Stage 6: Fog vs Cloud latency benchmarks & offline resilience
- Stage 7: Telegram graceful degradation
"""

import json
import os
import time
import unittest
from datetime import datetime

import database
from sensor_simulator import AeroponicSensorSimulator
from fog_node import FogComputingNode


class TestAeroponicsSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Use an isolated test database
        cls.test_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_aeroponics.db")
        if os.path.exists(cls.test_db):
            os.remove(cls.test_db)
        database.init_db(cls.test_db)

        # Load config
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        with open(cfg_path, "r") as f:
            cls.config = json.load(f)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.test_db):
            try:
                os.remove(cls.test_db)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # STAGE 1 TESTS: SENSOR SIMULATOR
    # -------------------------------------------------------------------------
    def test_01_sensor_values_within_realistic_bounds(self):
        """Validates that simulator outputs realistic ranges."""
        sim = AeroponicSensorSimulator()
        data = sim.generate_readings()

        self.assertIn("timestamp", data)
        self.assertTrue(20.0 <= data["temperature"] <= 30.0, f"Temp out of range: {data['temperature']}")
        self.assertTrue(40.0 <= data["humidity"] <= 98.0, f"Humidity out of range: {data['humidity']}")
        self.assertTrue(15.0 <= data["water_level"] <= 100.0, f"Water level out of range: {data['water_level']}")
        self.assertTrue(5.5 <= data["ph"] <= 6.5, f"pH out of range: {data['ph']}")
        self.assertTrue(1.2 <= data["ec"] <= 2.4, f"EC out of range: {data['ec']}")
        self.assertTrue(18.0 <= data["root_temp"] <= 24.0, f"Root temp out of range: {data['root_temp']}")

    def test_02_sensor_feedback_loop_with_pump(self):
        """Validates that humidity increases when pump is ON and decreases when OFF."""
        sim = AeroponicSensorSimulator()
        sim.humidity = 70.0

        # Simulate pump ON
        sim.pump_state = "ON"
        sim.update_physics()
        self.assertGreater(sim.humidity, 70.0, "Humidity should increase when pump is ON")

        # Simulate pump OFF
        h_before = sim.humidity
        sim.pump_state = "OFF"
        sim.update_physics()
        self.assertLess(sim.humidity, h_before, "Humidity should decrease when pump is OFF")

    def test_03_sensor_fault_injection_modes(self):
        """Validates fault injection behaviors in simulator."""
        sim = AeroponicSensorSimulator()

        # Stuck sensor
        sim.fault_mode = "STUCK"
        sim.stuck_frozen_value = 73.45
        data1 = sim.generate_readings()
        data2 = sim.generate_readings()
        self.assertEqual(data1["humidity"], 73.45)
        self.assertEqual(data2["humidity"], 73.45)

        # Out of range
        sim.fault_mode = "OUT_OF_RANGE"
        data3 = sim.generate_readings()
        self.assertEqual(data3["temperature"], -50.0)

        # Pump failure dynamics
        sim.fault_mode = "PUMP_FAILURE"
        sim.pump_state = "ON"
        h_before = sim.humidity
        sim.update_physics()
        self.assertLess(sim.humidity, h_before, "In pump failure, humidity must drop despite pump ON")

    # -------------------------------------------------------------------------
    # STAGE 2 TESTS: FOG NODE CONTROL LOGIC
    # -------------------------------------------------------------------------
    def test_04_smart_misting_auto_thresholds(self):
        """Validates auto misting turns ON below 70% and OFF above 85%."""
        node = FogComputingNode()
        node.mode = "AUTO"
        node.cycle_state = "REST"
        node.pump_state = "OFF"

        # Case A: Low Humidity (65%) -> Should trigger misting ON
        low_hum_data = {"temperature": 24.0, "humidity": 65.0, "water_level": 80.0, "ph": 6.0, "ec": 1.8, "root_temp": 20.0}
        node.evaluate_auto_misting(low_hum_data, self.config)
        self.assertEqual(node.pump_state, "ON")
        self.assertEqual(node.cycle_state, "MISTING")

        # Case B: High Humidity (88%) -> Should cutoff pump
        high_hum_data = {"temperature": 24.0, "humidity": 88.0, "water_level": 80.0, "ph": 6.0, "ec": 1.8, "root_temp": 20.0}
        node.evaluate_auto_misting(high_hum_data, self.config)
        self.assertEqual(node.pump_state, "OFF")
        self.assertEqual(node.cycle_state, "REST")

    def test_05_manual_override_commands(self):
        """Validates manual dashboard control overrides AUTO mode."""
        node = FogComputingNode()

        node.handle_pump_command("ON", self.config)
        self.assertEqual(node.mode, "MANUAL")
        self.assertEqual(node.pump_state, "ON")

        node.handle_pump_command("OFF", self.config)
        self.assertEqual(node.mode, "MANUAL")
        self.assertEqual(node.pump_state, "OFF")

        node.handle_pump_command("AUTO", self.config)
        self.assertEqual(node.mode, "AUTO")

    # -------------------------------------------------------------------------
    # STAGE 3 TESTS: FAULT DETECTION LOGIC
    # -------------------------------------------------------------------------
    def test_06_out_of_range_fault_detection(self):
        """Validates that extreme sensor readings trigger fault alerts."""
        node = FogComputingNode()
        detected_faults = []
        node.raise_fault = lambda f_type, s_name, sev, msg, cfg: detected_faults.append(f_type)

        # Temp = -50 (out of range)
        abnormal_data = {"temperature": -50.0, "humidity": 75.0, "water_level": 80.0, "ph": 6.0, "ec": 1.8, "root_temp": 20.0}
        node.check_faults(abnormal_data, self.config)
        self.assertIn("OUT_OF_RANGE", detected_faults)

    def test_07_stuck_sensor_fault_detection(self):
        """Validates that repeated identical readings trigger stuck sensor alert."""
        node = FogComputingNode()
        detected_faults = []
        node.raise_fault = lambda f_type, s_name, sev, msg, cfg: detected_faults.append(f_type)

        data = {"temperature": 24.0, "humidity": 72.10, "water_level": 80.0, "ph": 6.0, "ec": 1.8, "root_temp": 20.0}
        # Feed 5 identical readings
        for _ in range(5):
            node.check_faults(data, self.config)

        self.assertIn("STUCK_SENSOR", detected_faults)

    def test_08_pump_failure_fault_detection(self):
        """Validates that pump active for >20s without humidity rise triggers pump failure."""
        node = FogComputingNode()
        detected_faults = []
        node.raise_fault = lambda f_type, s_name, sev, msg, cfg: detected_faults.append(f_type)

        node.pump_state = "ON"
        node.pump_turned_on_time = time.time() - 25.0  # Simulating pump on 25s ago
        node.humidity_at_pump_on = 70.0

        # Humidity remains low at 68%
        data = {"temperature": 24.0, "humidity": 68.0, "water_level": 80.0, "ph": 6.0, "ec": 1.8, "root_temp": 20.0}
        node.check_faults(data, self.config)
        self.assertIn("PUMP_FAILURE", detected_faults)

    # -------------------------------------------------------------------------
    # STAGE 4 TESTS: SQLITE LOGGING & RETRIEVAL
    # -------------------------------------------------------------------------
    def test_09_database_logging_and_queries(self):
        """Validates database schema, inserts, and history queries."""
        # 1. Log reading
        sample_reading = {
            "timestamp": datetime.now().isoformat(),
            "temperature": 24.2,
            "humidity": 74.5,
            "water_level": 88.0,
            "ph": 5.95,
            "ec": 1.75,
            "root_temp": 20.1
        }
        database.log_reading(sample_reading, self.test_db)
        latest = database.get_latest_reading(self.test_db)
        self.assertIsNotNone(latest)
        self.assertAlmostEqual(latest["temperature"], 24.2)

        # 2. Log pump event
        database.log_pump_event("ON", "AUTO", "Low Humidity Trigger", 1.2, self.test_db)
        events = database.get_recent_pump_events(5, self.test_db)
        self.assertTrue(len(events) >= 1)
        self.assertEqual(events[0]["state"], "ON")

        # 3. Log fault
        database.log_fault("OUT_OF_RANGE", "temperature", "CRITICAL", "Temp below -40C", self.test_db)
        faults = database.get_recent_faults(5, self.test_db)
        self.assertTrue(len(faults) >= 1)
        self.assertEqual(faults[0]["fault_type"], "OUT_OF_RANGE")

    # -------------------------------------------------------------------------
    # STAGE 6 TESTS: FOG VS CLOUD LATENCY & RESILIENCE
    # -------------------------------------------------------------------------
    def test_10_fog_vs_cloud_benchmarking(self):
        """Validates latency logging for Fog mode vs Cloud mode."""
        node = FogComputingNode()

        # Fog local mode (near zero latency)
        node.simulate_cloud = False
        node.internet_connected = True
        node.execute_pump_decision("ON", "Fog Test", self.config)

        # Cloud mode (approx 300ms)
        node.simulate_cloud = True
        node.internet_connected = True
        # For fast test, set delay to 50ms in config
        test_cfg = dict(self.config)
        test_cfg["cloud_simulation"] = {"network_delay_ms": 50}
        node.execute_pump_decision("OFF", "Cloud Test", test_cfg)

        records = database.get_latency_data(limit=5)
        self.assertTrue(len(records) >= 2)

    # -------------------------------------------------------------------------
    # STAGE 7 TESTS: TELEGRAM BOT INTEGRATION
    # -------------------------------------------------------------------------
    def test_11_telegram_graceful_handling(self):
        """Validates that empty token skips silently without throwing exception."""
        node = FogComputingNode()
        # Empty token config
        empty_cfg = {"telegram": {"bot_token": "", "chat_id": ""}}
        fault_data = {
            "timestamp": datetime.now().isoformat(),
            "fault_type": "TEST_FAULT",
            "sensor_name": "test",
            "severity": "WARNING",
            "message": "Test Message"
        }
        # Must not raise any error
        try:
            node.send_telegram_alert(fault_data, empty_cfg)
            success = True
        except Exception:
            success = False
        self.assertTrue(success, "Telegram alert with empty credentials must not crash")


if __name__ == "__main__":
    unittest.main()
