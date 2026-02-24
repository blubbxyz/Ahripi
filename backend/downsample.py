#!/usr/bin/env python3
"""Daily downsampling agent. Run via cron at e.g. 3am."""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "../data/database.db")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def downsample():

    with db() as conn:

        # ---- CREATE TABLES IF THEY DON'T EXIST YET ----

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings_minutely (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minute TEXT NOT NULL,
                avg_temp REAL, min_temp REAL, max_temp REAL,
                avg_humidity REAL, min_humidity REAL, max_humidity REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour TEXT NOT NULL,
                avg_temp REAL, min_temp REAL, max_temp REAL,
                avg_humidity REAL, min_humidity REAL, max_humidity REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_readings_minutely (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minute TEXT NOT NULL,
                avg_cpu REAL, min_cpu REAL, max_cpu REAL,
                avg_ram REAL, min_ram REAL, max_ram REAL,
                avg_ram_speed REAL, min_ram_speed REAL, max_ram_speed REAL,
                avg_core_temp REAL, min_core_temp REAL, max_core_temp REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_readings_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour TEXT NOT NULL,
                avg_cpu REAL, min_cpu REAL, max_cpu REAL,
                avg_ram REAL, min_ram REAL, max_ram REAL,
                avg_ram_speed REAL, min_ram_speed REAL, max_ram_speed REAL,
                avg_core_temp REAL, min_core_temp REAL, max_core_temp REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_readings_minutely (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minute TEXT NOT NULL,
                avg_rx_kbps REAL, min_rx_kbps REAL, max_rx_kbps REAL,
                avg_tx_kbps REAL, min_tx_kbps REAL, max_tx_kbps REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_readings_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour TEXT NOT NULL,
                avg_rx_kbps REAL, min_rx_kbps REAL, max_rx_kbps REAL,
                avg_tx_kbps REAL, min_tx_kbps REAL, max_tx_kbps REAL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ---- SENSORS ----

        # raw -> minutely (older than 30 days)
        conn.execute("""
            INSERT INTO sensor_readings_minutely
                (minute, avg_temp, min_temp, max_temp,
                 avg_humidity, min_humidity, max_humidity)
            SELECT
                strftime('%Y-%m-%d %H:%M', recorded_at),
                AVG(temp), MIN(temp), MAX(temp),
                AVG(humidity), MIN(humidity), MAX(humidity)
            FROM sensor_readings
            WHERE recorded_at < datetime('now', '-30 days')
            GROUP BY strftime('%Y-%m-%d %H:%M', recorded_at)
        """)
        conn.execute("DELETE FROM sensor_readings WHERE recorded_at < datetime('now', '-30 days')")

        # minutely -> hourly (older than 1 year)
        conn.execute("""
            INSERT INTO sensor_readings_hourly
                (hour, avg_temp, min_temp, max_temp,
                 avg_humidity, min_humidity, max_humidity)
            SELECT
                strftime('%Y-%m-%d %H', minute),
                AVG(avg_temp), MIN(min_temp), MAX(max_temp),
                AVG(avg_humidity), MIN(min_humidity), MAX(max_humidity)
            FROM sensor_readings_minutely
            WHERE minute < strftime('%Y-%m-%d %H:%M', 'now', '-365 days')
            GROUP BY strftime('%Y-%m-%d %H', minute)
        """)
        conn.execute("""
            DELETE FROM sensor_readings_minutely
            WHERE minute < strftime('%Y-%m-%d %H:%M', 'now', '-365 days')
        """)

        # ---- SYSTEM ----

        # raw -> minutely (older than 30 days)
        conn.execute("""
            INSERT INTO system_readings_minutely
                (minute, avg_cpu, min_cpu, max_cpu,
                 avg_ram, min_ram, max_ram,
                 avg_ram_speed, min_ram_speed, max_ram_speed,
                 avg_core_temp, min_core_temp, max_core_temp)
            SELECT
                strftime('%Y-%m-%d %H:%M', recorded_at),
                AVG(cpu), MIN(cpu), MAX(cpu),
                AVG(ram), MIN(ram), MAX(ram),
                AVG(ram_speed), MIN(ram_speed), MAX(ram_speed),
                AVG(core_temp), MIN(core_temp), MAX(core_temp)
            FROM system_readings
            WHERE recorded_at < datetime('now', '-30 days')
            GROUP BY strftime('%Y-%m-%d %H:%M', recorded_at)
        """)
        conn.execute("DELETE FROM system_readings WHERE recorded_at < datetime('now', '-30 days')")

        # minutely -> hourly (older than 1 year)
        conn.execute("""
            INSERT INTO system_readings_hourly
                (hour, avg_cpu, min_cpu, max_cpu,
                 avg_ram, min_ram, max_ram,
                 avg_ram_speed, min_ram_speed, max_ram_speed,
                 avg_core_temp, min_core_temp, max_core_temp)
            SELECT
                strftime('%Y-%m-%d %H', minute),
                AVG(avg_cpu), MIN(min_cpu), MAX(max_cpu),
                AVG(avg_ram), MIN(min_ram), MAX(max_ram),
                AVG(avg_ram_speed), MIN(min_ram_speed), MAX(max_ram_speed),
                AVG(avg_core_temp), MIN(min_core_temp), MAX(max_core_temp)
            FROM system_readings_minutely
            WHERE minute < strftime('%Y-%m-%d %H:%M', 'now', '-365 days')
            GROUP BY strftime('%Y-%m-%d %H', minute)
        """)
        conn.execute("""
            DELETE FROM system_readings_minutely
            WHERE minute < strftime('%Y-%m-%d %H:%M', 'now', '-365 days')
        """)

        # ---- NETWORK ----

        # raw -> minutely (older than 30 days)
        conn.execute("""
            INSERT INTO network_readings_minutely
                (minute, avg_rx_kbps, min_rx_kbps, max_rx_kbps,
                 avg_tx_kbps, min_tx_kbps, max_tx_kbps)
            SELECT
                strftime('%Y-%m-%d %H:%M', recorded_at),
                AVG(rx_kbps), MIN(rx_kbps), MAX(rx_kbps),
                AVG(tx_kbps), MIN(tx_kbps), MAX(tx_kbps)
            FROM network_readings
            WHERE recorded_at < datetime('now', '-30 days')
            GROUP BY strftime('%Y-%m-%d %H:%M', recorded_at)
        """)
        conn.execute("DELETE FROM network_readings WHERE recorded_at < datetime('now', '-30 days')")

        # minutely -> hourly (older than 1 year)
        conn.execute("""
            INSERT INTO network_readings_hourly
                (hour, avg_rx_kbps, min_rx_kbps, max_rx_kbps,
                 avg_tx_kbps, min_tx_kbps, max_tx_kbps)
            SELECT
                strftime('%Y-%m-%d %H', minute),
                AVG(avg_rx_kbps), MIN(min_rx_kbps), MAX(max_rx_kbps),
                AVG(avg_tx_kbps), MIN(min_tx_kbps), MAX(max_tx_kbps)
            FROM network_readings_minutely
            WHERE minute < strftime('%Y-%m-%d %H:%M', 'now', '-365 days')
            GROUP BY strftime('%Y-%m-%d %H', minute)
        """)
        conn.execute("""
            DELETE FROM network_readings_minutely
            WHERE minute < strftime('%Y-%m-%d %H:%M', 'now', '-365 days')
        """)

        print("Downsampling complete.")

if __name__ == "__main__":
    downsample()