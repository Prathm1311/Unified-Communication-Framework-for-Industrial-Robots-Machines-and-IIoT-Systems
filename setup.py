from setuptools import find_packages, setup
import os

package_name = "robot_bridge"

# Collect all YAML files in config/
config_files = [
    os.path.join("config", f)
    for f in os.listdir("config")
    if f.endswith(".yaml") or f.endswith(".yml")
]

# Collect all launch files
launch_files = [
    os.path.join("launch", f)
    for f in os.listdir("launch")
    if f.endswith(".py") or f.endswith(".xml") or f.endswith(".yaml")
]

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["tests", "tests.*"]),
    data_files=[
        # Required by ament
        ("share/ament_index/resource_index/packages", ["resource/robot_bridge"]),
        (f"share/{package_name}", ["package.xml"]),
        # Config and launch files
        (f"share/{package_name}/config",  config_files),
        (f"share/{package_name}/launch",  launch_files),
    ],
    install_requires=[
        "paho-mqtt>=1.6",
        "fastapi>=0.100",
        "uvicorn[standard]>=0.23",
        "pyyaml>=6.0",
        "pydantic>=2.0",
    ],
    zip_safe=True,
    maintainer="RobotBridge Contributors",
    maintainer_email="robotbridge@example.com",
    description=(
        "Middleware bridge between ROS2-based robots "
        "and external MQTT/REST applications."
    ),
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            # Installs the `robot_bridge` command that can be run directly
            "robot_bridge = robot_bridge.main:main",
        ],
    },
)
