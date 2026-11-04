"""
Aeroponics Fog IoT System - One-Click Launcher (run_all.py)
===========================================================
Spawns all three architecture tiers concurrently:
1. MQTT Broker Check (Mosquitto on localhost:1883 or auto-fallback)
2. Fog Computing Node (fog_node.py)
3. Sensor Simulator (sensor_simulator.py)
4. Streamlit Interactive Dashboard (dashboard.py)

Handles clean shutdown on Ctrl+C.
"""

import os
import signal
import socket
import subprocess
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXEC = sys.executable


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Checks whether the MQTT port is accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def main():
    print("=" * 70)
    print("[AEROPONICS] LAUNCHING FOG-BASED IOT SYSTEM FOR VERTICAL AEROPONICS")
    print("=" * 70)

    processes = []

    # 1. Verify MQTT Broker availability
    broker_proc = None
    if not is_port_in_use(1883):
        print("[Notice] Port 1883 is not active yet.")
        print("[Notice] Launching local broker (broker.py) on localhost:1883...")
        try:
            broker_proc = subprocess.Popen(
                [PYTHON_EXEC, os.path.join(PROJECT_DIR, "broker.py")],
                cwd=PROJECT_DIR
            )
            processes.append(broker_proc)
            time.sleep(2.5)
        except Exception as e:
            print(f"Could not launch broker.py: {e}")

        if not is_port_in_use(1883):
            print("\n" + "!" * 70)
            print("⚠️ NOTE: If you are using Mosquitto, ensure it is running with:")
            print("   mosquitto -v")
            print("!" * 70 + "\n")
        else:
            print("[OK] Local MQTT Broker is running on localhost:1883.")
    else:
        print("[OK] Existing MQTT Broker detected on localhost:1883.")

    # 2. Launch Fog Computing Node
    print("\n[1/3] Starting Fog Computing Node (fog_node.py)...")
    fog_proc = subprocess.Popen(
        [PYTHON_EXEC, os.path.join(PROJECT_DIR, "fog_node.py")],
        cwd=PROJECT_DIR
    )
    processes.append(fog_proc)
    time.sleep(1.5)

    # 3. Launch Sensor Simulator
    print("[2/3] Starting Aeroponic Sensor Simulator (sensor_simulator.py)...")
    sim_proc = subprocess.Popen(
        [PYTHON_EXEC, os.path.join(PROJECT_DIR, "sensor_simulator.py")],
        cwd=PROJECT_DIR
    )
    processes.append(sim_proc)
    time.sleep(1.5)

    # 4. Launch Streamlit Web Dashboard
    print("[3/3] Starting Web Dashboard (dashboard.py)...")
    dash_proc = subprocess.Popen(
        [PYTHON_EXEC, "-m", "streamlit", "run", os.path.join(PROJECT_DIR, "dashboard.py")],
        cwd=PROJECT_DIR
    )
    processes.append(dash_proc)

    print("\n" + "=" * 70)
    print("[RUNNING] ALL COMPONENTS ACTIVE!")
    print("* Fog Node: Monitoring sensors, misting control, and fault detection")
    print("* Sensor Simulator: Transmitting aeroponic telemetry every 2s")
    print("* Web Dashboard: Opening in your browser (usually http://localhost:8501)")
    print("\nPress Ctrl+C at any time in this window to stop all processes.")
    print("=" * 70 + "\n")

    try:
        while True:
            time.sleep(1)
            # Check if any process terminated unexpectedly
            for p in processes:
                if p.poll() is not None:
                    # If dashboard closed or process died
                    pass
    except KeyboardInterrupt:
        print("\n[Shutdown] Stopping all aeroponics processes...")
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass
        print("[Shutdown] All processes terminated safely. Goodbye!")


if __name__ == "__main__":
    main()
