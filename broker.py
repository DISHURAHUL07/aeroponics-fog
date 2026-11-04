"""
Optional Lightweight MQTT Broker (broker.py)
=============================================
Provides a local MQTT broker on 127.0.0.1:1883.

Usage:
    python broker.py

If Mosquitto is already installed and running, you do not need this file.
This is provided as a zero-setup convenience for students.
"""

import asyncio
import sys
from amqtt.broker import Broker

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BROKER_CONFIG = {
    "listeners": {
        "default": {
            "type": "tcp",
            "bind": "127.0.0.1:1883",
            "max_connections": 50,
        }
    },
    "sys_interval": 20,
    "auth": {
        "allow-anonymous": True,
    }
}


async def main_async():
    print("=" * 65)
    print("[BROKER] STARTING LOCAL MQTT BROKER ON 127.0.0.1:1883...")
    print("=" * 65)
    broker = Broker(BROKER_CONFIG)
    await broker.start()
    print("[OK] MQTT Broker is ACTIVE and accepting connections on port 1883!")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(1)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await broker.shutdown()
        print("[MQTT Broker] Stopped cleanly.")


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[MQTT Broker] Exited.")


if __name__ == "__main__":
    main()
