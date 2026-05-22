# RobotBridge – ROS2 to MQTT/REST Communication Bridge

> Student Robotics Project  
> Developed as part of my robotics and automation learning journey.

---

# Project Overview

RobotBridge is a middleware application that connects a ROS2 robot with external applications such as web dashboards, mobile applications, and cloud services.

Robots usually communicate using ROS2 topics, while external systems commonly use MQTT or REST APIs. Because of this difference, direct communication can be difficult.

The purpose of RobotBridge is to act as a translator between these systems. It receives data from ROS2, converts it into JSON format, and sends it through MQTT or REST APIs. It can also receive commands from MQTT or REST and publish them back to ROS2 topics.

This project helped me learn:

- ROS2 communication
- MQTT messaging
- REST API development
- FastAPI
- Software architecture
- Robotics system integration

---

# Problem Statement

A robot may publish information such as:

- Position
- Battery status
- Velocity
- Sensor data

using ROS2 topics.

However, a dashboard or cloud application cannot directly understand ROS2 messages.

RobotBridge solves this problem by creating a communication layer between:

```text
ROS2 Robot  <---->  RobotBridge  <---->  External Applications
```

---

# System Architecture

```text
+----------------+
|   ROS2 Robot   |
+----------------+
        |
        v
+----------------+
|   RobotBridge  |
|----------------|
| ROS2 Adapter   |
| MQTT Adapter   |
| REST Adapter   |
+----------------+
        |
        +----------------+
        |                |
        v                v
+-------------+   +-------------+
| MQTT Broker |   | REST Client |
+-------------+   +-------------+
```

---

# Features

## ROS2 Integration

- Subscribe to ROS2 topics
- Publish messages to ROS2 topics
- Support multiple topic mappings

## MQTT Communication

- Publish robot telemetry
- Receive robot commands
- Connect to any MQTT broker

## REST API

- View robot telemetry
- Send commands through HTTP requests
- Swagger UI documentation

## Configuration Based

Mappings are stored in a YAML file so new topics can be added without modifying code.

---

# Project Structure

```text
robot_bridge/

├── robot_bridge/
│   ├── main.py
│   ├── bridge_manager.py
│   │
│   ├── adapters/
│   │   ├── ros2_adapter.py
│   │   ├── mqtt_adapter.py
│   │   └── rest_adapter.py
│   │
│   ├── models/
│   │   └── message.py
│   │
│   └── utils/
│       └── serializer.py
│
├── config/
│   └── bridge_config.yaml
│
├── launch/
│   └── robot_bridge.launch.py
│
├── tests/
│
├── requirements.txt
├── setup.py
└── package.xml
```

---

# Technologies Used

| Technology | Purpose |
|------------|----------|
| ROS2 Humble | Robot communication |
| Python | Main programming language |
| FastAPI | REST API development |
| MQTT | Lightweight messaging |
| Paho MQTT | MQTT client library |
| YAML | Configuration management |
| Pytest | Unit testing |

---

# Installation

## Step 1: Create a ROS2 Workspace

```bash
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws
```

## Step 2: Copy the Project

```bash
cp -r robot_bridge ~/ros2_ws/src/
```

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 4: Build the Package

```bash
cd ~/ros2_ws

colcon build --packages-select robot_bridge
```

## Step 5: Source the Workspace

```bash
source /opt/ros/humble/setup.bash

source ~/ros2_ws/install/setup.bash
```

## Step 6: Start an MQTT Broker

```bash
sudo apt install mosquitto mosquitto-clients

sudo systemctl start mosquitto
```

---

# Configuration

RobotBridge uses a YAML configuration file.

Example:

```yaml
mqtt:
  broker_host: localhost
  broker_port: 1883

rest:
  host: "0.0.0.0"
  port: 8000

mappings:

  - name: robot_pose

    ros2_topic: /robot/pose

    mqtt_topic: robot/telemetry/pose

    rest_endpoint: /telemetry/pose

    direction: ros2_to_external
```

This mapping means:

1. Read data from `/robot/pose`
2. Convert it into JSON
3. Send it to MQTT
4. Make it available through a REST API

---

# Running the Project

### Using ROS2 Launch

```bash
ros2 launch robot_bridge robot_bridge.launch.py
```

### Running Directly

```bash
robot_bridge --config config/bridge_config.yaml
```

---

# Testing MQTT Communication

### Subscribe to Robot Topics

```bash
mosquitto_sub -h localhost -t "robot/#" -v
```

### Publish a Command

```bash
mosquitto_pub \
-h localhost \
-t "robot/command/cmd_vel" \
-m '{"data":{"linear":{"x":0.5},"angular":{"z":0.2}}}'
```

---

# Testing the REST API

### Health Check

```bash
curl http://localhost:8000/health
```

### View Telemetry

```bash
curl http://localhost:8000/telemetry
```

### Send a Command

```bash
curl -X POST http://localhost:8000/command/cmd_vel
```

### API Documentation

Open in your browser:

```text
http://localhost:8000/docs
```

---

# What I Learned

Through this project I learned:

- ROS2 publisher and subscriber communication
- Message serialization and deserialization
- MQTT publish/subscribe architecture
- REST API development using FastAPI
- YAML-based configuration management
- Robotics middleware design
- Modular software architecture
- Unit testing with pytest

---

# Future Improvements

Planned improvements for future versions:

- User authentication for REST APIs
- MQTT TLS security support
- Web dashboard integration
- Database storage for telemetry
- Docker deployment
- Multi-robot support
- Cloud connectivity (AWS/Azure)
- Real-time monitoring dashboard

---

# Conclusion

RobotBridge is a robotics middleware project that enables communication between ROS2 robots and external systems using MQTT and REST APIs.

The project demonstrates practical skills in:

- Robotics software development
- Communication protocols
- API development
- Middleware architecture
- System integration

It provides a strong foundation for larger robotics, Industry 4.0, IoT, and autonomous system projects.

---
