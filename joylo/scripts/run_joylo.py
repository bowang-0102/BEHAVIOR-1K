import glob
import yaml
from dataclasses import dataclass
from typing import Optional, Tuple, Literal

import numpy as np
import tyro

from gello.agents.bimanual_agent import (
    ROBOT_TELEOP_CONFIGS,
    BimanualAgent,
    MotorFeedbackConfig,
)
from gello.agents.dynamixel_arm_agent import DynamixelArmAgent, DynamixelRobotConfig
from gello.agents.joycon_agent import JoyconAgent
from gello.env import RobotEnv
from gello.robots.base_robot import PrintRobot
from gello.utils.zmq_utils import ZMQRobotClient
from gello import REPO_DIR


def print_color(*args, color=None, attrs=(), **kwargs):
    import termcolor

    if len(args) > 0:
        args = tuple(termcolor.colored(arg, color=color, attrs=attrs) for arg in args)
    print(*args, **kwargs)


@dataclass
class Args:
    robot_port: int = 6001
    hostname: str = "127.0.0.1"
    hz: int = 100
    start_joints: Optional[Tuple[float, ...]] = None
    gello_model: str = "r1pro"
    gello_name: str = "default"
    gello_port: Optional[str] = None
    mock: bool = False
    damping_motor_kp: float = 0.3
    motor_feedback_type: str = "NONE"
    use_joycons: bool = True


def main(args):
    assert args.gello_model in ROBOT_TELEOP_CONFIGS, (
        f"Unsupported gello model: {args.gello_model}"
    )
    bimanual_config = ROBOT_TELEOP_CONFIGS[args.gello_model]

    if args.mock:
        robot_client = PrintRobot(bimanual_config.joints_per_arm * 2, dont_print=True)
    else:
        robot_client = ZMQRobotClient(port=args.robot_port, host=args.hostname)

    env = RobotEnv(robot_client, control_rate_hz=args.hz)

    # Find gello port
    gello_port = args.gello_port
    if gello_port is None:
        import platform

        if platform.system().lower() == "linux":
            usb_ports = glob.glob("/dev/serial/by-id/*")
        elif platform.system().lower() == "darwin":
            usb_ports = glob.glob("/dev/cu.usbserial-*")
        else:
            raise ValueError(f"Unsupported platform {platform.system()}")
        print(f"Found {len(usb_ports)} ports")
        if len(usb_ports) > 0:
            gello_port = usb_ports[0]
            print(f"using port {gello_port}")
        else:
            raise ValueError("No gello port found, please specify one or plug in gello")

    # Read joint config from yaml
    with open(f"{REPO_DIR}/configs/joint_config_{args.gello_name}.yaml", "r") as file:
        joint_config = yaml.load(file, Loader=yaml.SafeLoader)

    num_motors = bimanual_config.motors_per_arm * 2
    dynamixel_config = DynamixelRobotConfig(
        joint_ids=tuple(np.arange(num_motors).tolist()),
        joint_offsets=[np.deg2rad(x) for x in joint_config["joints"]["offsets"]],
        joint_signs=joint_config["joints"]["signs"],
        gripper_config=None,
    )

    # Default start joints
    start_joints = args.start_joints
    if start_joints is None:
        start_joints = bimanual_config.start_joints.copy()

    # Create JoyCon agent
    joycon_agent = None
    if args.use_joycons:
        joycon_agent = JoyconAgent(
            calibration_dir=f"{REPO_DIR}/configs",
            deadzone_threshold=0.2,
            max_translation=0.5,
            max_rotation=0.5,
            max_trunk_translate=0.2,
            max_trunk_tilt=0.2,
            enable_rumble=False,
        )

    # Create arm agent and bimanual agent
    arm_agent = DynamixelArmAgent(
        port=gello_port,
        dynamixel_config=dynamixel_config,
        start_joints=start_joints,
        damping_motor_kp=args.damping_motor_kp,
    )

    agent = BimanualAgent(
        config=bimanual_config,
        arm_agent=arm_agent,
        joycon_agent=joycon_agent,
        motor_feedback_type=MotorFeedbackConfig[args.motor_feedback_type],
    )

    agent.start()

    print("Aligning GELLO. Hardware will move.")
    agent.reset()

    obs = env.get_obs()
    collection = obs.get("session_collection", False)
    print_color(
        f"\nJoyLo ({args.gello_model.upper()}) - {'Memory Bench collection' if collection else 'teleoperation'}",
        color="magenta",
        attrs=("bold",),
    )
    if collection:
        print(
            "JoyCon controls:\n"
            "  Right: Start or resume recording.\n"
            "  Left: Check and save the current subtask.\n"
            "  X: Save a checkpoint while recording, resume while paused, or confirm a selected rollback.\n"
            "  Y: Select a checkpoint to restore.\n"
            "  Home: Load the next instance. Capture: Exit collection.\n"
            "Keyboard controls (click the simulator window first):\n"
            "  X: Start or resume. P: Pause. R: Rerecord this instance. Esc: Exit."
        )
    else:
        print(
            "JoyCon: X checkpoint / resume / confirm rollback | Y reset arms / select checkpoint\n"
            "        Left/Right gripper lights | Home reset | Capture exit\n"
            "Keyboard (simulator focused): X resume | P pause | R reset | Esc exit"
        )
    print(
        "Motion: left stick move | right stick turn / tilt torso | Up/Down torso height\n"
        "        ZL/ZR grippers | L/R lock wrists | -/+ lock upper joints\n"
        "View: B camera | A object visibility / highlights\n"
        "Hardware connected. Follow the simulator prompts.\n"
    )
    while True:
        action = agent.act(obs)
        obs = env.step(action)


if __name__ == "__main__":
    main(tyro.cli(Args))
