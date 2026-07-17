#!/usr/bin/env python3
"""Headless xArm7 MoveIt fake launch for cloud-labs MuJoCo tests.

The upstream ``xarm7_moveit_fake.launch.py`` always starts RViz. Over SSH that
causes RViz to crash and the launch file intentionally shuts down MoveIt. This
launch keeps only the pieces the HTTP sidecar needs for plan-only requests:
robot description, static world transform, and ``move_group``.
"""

from __future__ import annotations

import os
import yaml

from ament_index_python import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from uf_ros_lib.moveit_configs_builder import MoveItConfigsBuilder
from uf_ros_lib.uf_robot_utils import generate_ros2_control_params_temp_file


def launch_setup(context, *args, **kwargs):  # noqa: ANN001, ANN202
    del args, kwargs

    dof = LaunchConfiguration("dof", default=7)
    robot_type = LaunchConfiguration("robot_type", default="xarm")
    prefix = LaunchConfiguration("prefix", default="")
    hw_ns = LaunchConfiguration("hw_ns", default="xarm")
    limited = LaunchConfiguration("limited", default=True)
    effort_control = LaunchConfiguration("effort_control", default=False)
    velocity_control = LaunchConfiguration("velocity_control", default=False)
    model1300 = LaunchConfiguration("model1300", default=False)
    robot_sn = LaunchConfiguration("robot_sn", default="")
    attach_to = LaunchConfiguration("attach_to", default="world")
    attach_xyz = LaunchConfiguration("attach_xyz", default='"0 0 0"')
    attach_rpy = LaunchConfiguration("attach_rpy", default='"0 0 0"')
    mesh_suffix = LaunchConfiguration("mesh_suffix", default="stl")
    kinematics_suffix = LaunchConfiguration("kinematics_suffix", default="")
    gripper_version = LaunchConfiguration("gripper_version", default="G1")

    add_gripper = LaunchConfiguration("add_gripper", default=True)
    add_vacuum_gripper = LaunchConfiguration("add_vacuum_gripper", default=False)
    add_bio_gripper = LaunchConfiguration("add_bio_gripper", default=False)
    add_realsense_d435i = LaunchConfiguration("add_realsense_d435i", default=False)
    add_d435i_links = LaunchConfiguration("add_d435i_links", default=True)
    add_other_geometry = LaunchConfiguration("add_other_geometry", default=False)
    geometry_type = LaunchConfiguration("geometry_type", default="box")
    geometry_mass = LaunchConfiguration("geometry_mass", default=0.1)
    geometry_height = LaunchConfiguration("geometry_height", default=0.1)
    geometry_radius = LaunchConfiguration("geometry_radius", default=0.1)
    geometry_length = LaunchConfiguration("geometry_length", default=0.1)
    geometry_width = LaunchConfiguration("geometry_width", default=0.1)
    geometry_mesh_filename = LaunchConfiguration("geometry_mesh_filename", default="")
    geometry_mesh_origin_xyz = LaunchConfiguration(
        "geometry_mesh_origin_xyz",
        default='"0 0 0"',
    )
    geometry_mesh_origin_rpy = LaunchConfiguration(
        "geometry_mesh_origin_rpy",
        default='"0 0 0"',
    )
    geometry_mesh_tcp_xyz = LaunchConfiguration(
        "geometry_mesh_tcp_xyz",
        default='"0 0 0"',
    )
    geometry_mesh_tcp_rpy = LaunchConfiguration(
        "geometry_mesh_tcp_rpy",
        default='"0 0 0"',
    )

    ros_namespace = ""
    xarm_type = f"{robot_type.perform(context)}{dof.perform(context)}"
    ros2_control_params = generate_ros2_control_params_temp_file(
        os.path.join(
            get_package_share_directory("xarm_controller"),
            "config",
            f"{xarm_type}_controllers.yaml",
        ),
        prefix=prefix.perform(context),
        add_gripper=add_gripper.perform(context) in ("True", "true"),
        add_bio_gripper=add_bio_gripper.perform(context) in ("True", "true"),
        ros_namespace=ros_namespace,
        robot_type=robot_type.perform(context),
    )

    moveit_config = MoveItConfigsBuilder(
        context=context,
        controllers_name="fake_controllers",
        dof=dof,
        robot_type=robot_type,
        prefix=prefix,
        hw_ns=hw_ns,
        limited=limited,
        effort_control=effort_control,
        velocity_control=velocity_control,
        model1300=model1300,
        robot_sn=robot_sn,
        attach_to=attach_to,
        attach_xyz=attach_xyz,
        attach_rpy=attach_rpy,
        mesh_suffix=mesh_suffix,
        kinematics_suffix=kinematics_suffix,
        ros2_control_plugin="uf_robot_hardware/UFRobotFakeSystemHardware",
        ros2_control_params=ros2_control_params,
        gripper_version=gripper_version,
        add_gripper=add_gripper,
        add_vacuum_gripper=add_vacuum_gripper,
        add_bio_gripper=add_bio_gripper,
        add_realsense_d435i=add_realsense_d435i,
        add_d435i_links=add_d435i_links,
        add_other_geometry=add_other_geometry,
        geometry_type=geometry_type,
        geometry_mass=geometry_mass,
        geometry_height=geometry_height,
        geometry_radius=geometry_radius,
        geometry_length=geometry_length,
        geometry_width=geometry_width,
        geometry_mesh_filename=geometry_mesh_filename,
        geometry_mesh_origin_xyz=geometry_mesh_origin_xyz,
        geometry_mesh_origin_rpy=geometry_mesh_origin_rpy,
        geometry_mesh_tcp_xyz=geometry_mesh_tcp_xyz,
        geometry_mesh_tcp_rpy=geometry_mesh_tcp_rpy,
    ).to_moveit_configs()
    moveit_config_dict = moveit_config.to_dict()

    robot_description_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution(
                [
                    FindPackageShare("xarm_description"),
                    "launch",
                    "_robot_description.launch.py",
                ]
            )
        ),
        launch_arguments={
            "robot_description": yaml.dump(moveit_config.robot_description),
        }.items(),
    )

    move_group_node = Node(
        package="moveit_ros_move_group",
        executable="move_group",
        output="screen",
        parameters=[moveit_config_dict, {"use_sim_time": False}],
    )

    xyz = attach_xyz.perform(context)[1:-1].split(" ")
    rpy = attach_rpy.perform(context)[1:-1].split(" ")
    static_tf = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="static_transform_publisher",
        output="screen",
        arguments=xyz + rpy + [attach_to.perform(context), f"{prefix.perform(context)}link_base"],
        parameters=[{"use_sim_time": False}],
    )

    return [robot_description_launch, static_tf, move_group_node]


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([OpaqueFunction(function=launch_setup)])
