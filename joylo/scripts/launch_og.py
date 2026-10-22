import tyro
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass
class Args:
    robot: str = "r1pro"  # Robot type
    robot_port: int = 6001
    hostname: str = "127.0.0.1"
    recording_path: Optional[str] = None
    task_name: Optional[str] = None
    partial_load: Optional[bool] = True
    instance_id: Optional[int] = None
    ghosting: Optional[bool] = True
    attachment_joint_visuals: Optional[bool] = None
    session_config: Optional[str] = None


def launch_robot_server(args: Args):
    from gello.robots.og_robot import OGRobotServer

    server = OGRobotServer(
        robot=args.robot,
        port=args.robot_port,
        host=args.hostname,
        recording_path=args.recording_path,
        task_name=args.task_name,
        partial_load=args.partial_load,
        instance_id=args.instance_id,
        ghosting=args.ghosting,
        attachment_joint_visuals=args.attachment_joint_visuals,
    )
    server.serve()


def main(args):
    if args.session_config is not None:
        from memory_bench.collection.collect_session import collect_session
        from memory_bench.session import Session

        session = Session.load(args.session_config)
        collect_session(session, host=args.hostname, port=args.robot_port)
        return
    launch_robot_server(args)


if __name__ == "__main__":
    main(tyro.cli(Args))
