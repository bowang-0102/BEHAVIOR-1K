#!/usr/bin/env python3
"""Read the current state of Dynamixel servos without changing them.

This script deliberately uses the Dynamixel SDK directly instead of the
project's DynamixelDriver.  DynamixelDriver disables torque while it is being
constructed, which is undesirable for a read-only diagnostic tool.

Examples:
    python joylo/scripts/inspect_servo_state.py --port /dev/ttyUSB1 --model r1pro
    python joylo/scripts/inspect_servo_state.py \
        --port /dev/ttyUSB1 --ids 0 1 2 3
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from typing import Optional

from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler


# Dynamixel X-series / Protocol 2.0 control-table addresses used by JoyLo.
ADDR_OPERATING_MODE = 11
ADDR_TORQUE_ENABLE = 64
ADDR_HARDWARE_ERROR = 70
ADDR_GOAL_CURRENT = 102
ADDR_PRESENT_CURRENT = 126
ADDR_PRESENT_VELOCITY = 128
ADDR_PRESENT_POSITION = 132

MODEL_IDS = {
    "r1": range(16),
    "r1pro": range(18),
}

OPERATING_MODES = {
    0: "CURRENT",
    1: "VELOCITY",
    3: "POSITION",
    4: "EXTENDED_POSITION",
    5: "CURRENT_BASED_POSITION",
    16: "PWM",
}

HARDWARE_ERROR_FLAGS = {
    0x01: "INPUT_VOLTAGE",
    0x04: "OVERHEATING",
    0x08: "ENCODER",
    0x10: "ELECTRICAL_SHOCK",
    0x20: "OVERLOAD",
}


@dataclass
class ServoState:
    servo_id: int
    torque_enabled: Optional[bool] = None
    operating_mode: Optional[int] = None
    operating_mode_name: Optional[str] = None
    hardware_error: Optional[int] = None
    goal_current_raw: Optional[int] = None
    present_current_raw: Optional[int] = None
    present_velocity_raw: Optional[int] = None
    present_position_raw: Optional[int] = None
    hardware_error_flags: Optional[list[str]] = None
    packet_error_code: Optional[int] = None
    packet_error: Optional[str] = None
    error: Optional[str] = None


def signed(value: int, bits: int) -> int:
    """Convert an unsigned two's-complement register value to a signed value."""
    sign_bit = 1 << (bits - 1)
    return value - (1 << bits) if value & sign_bit else value


def read_register(
    packet_handler: PacketHandler,
    port_handler: PortHandler,
    servo_id: int,
    address: int,
    width: int,
) -> tuple[int, Optional[int], Optional[str]]:
    """Read one register and raise a useful error on communication failure."""
    if width == 1:
        value, comm_result, packet_error = packet_handler.read1ByteTxRx(
            port_handler, servo_id, address
        )
    elif width == 2:
        value, comm_result, packet_error = packet_handler.read2ByteTxRx(
            port_handler, servo_id, address
        )
    elif width == 4:
        value, comm_result, packet_error = packet_handler.read4ByteTxRx(
            port_handler, servo_id, address
        )
    else:
        raise ValueError(f"Unsupported register width: {width}")

    if comm_result != COMM_SUCCESS:
        message = packet_handler.getTxRxResult(comm_result)
        raise RuntimeError(f"communication failed: {message}")
    packet_error_message = None
    if packet_error != 0:
        packet_error_message = packet_handler.getRxPacketError(packet_error)
    return value, packet_error if packet_error != 0 else None, packet_error_message


def read_servo_state(
    packet_handler: PacketHandler,
    port_handler: PortHandler,
    servo_id: int,
) -> ServoState:
    """Read all diagnostic registers for one servo."""
    state = ServoState(servo_id=servo_id)
    packet_errors: list[str] = []

    def read(address: int, width: int) -> int:
        value, packet_error_code, packet_error = read_register(
            packet_handler, port_handler, servo_id, address, width
        )
        if packet_error_code is not None and state.packet_error_code is None:
            state.packet_error_code = packet_error_code
        if packet_error and packet_error not in packet_errors:
            packet_errors.append(packet_error)
        return value

    try:
        state.torque_enabled = bool(read(ADDR_TORQUE_ENABLE, 1))

        mode = read(ADDR_OPERATING_MODE, 1)
        state.operating_mode = mode
        state.operating_mode_name = OPERATING_MODES.get(mode, f"UNKNOWN({mode})")

        state.hardware_error = read(ADDR_HARDWARE_ERROR, 1)
        state.hardware_error_flags = [
            name
            for bit, name in HARDWARE_ERROR_FLAGS.items()
            if state.hardware_error & bit
        ]
        state.goal_current_raw = signed(read(ADDR_GOAL_CURRENT, 2), 16)
        state.present_current_raw = signed(read(ADDR_PRESENT_CURRENT, 2), 16)
        state.present_velocity_raw = signed(read(ADDR_PRESENT_VELOCITY, 4), 32)
        state.present_position_raw = read(ADDR_PRESENT_POSITION, 4)
    except Exception as exc:  # Keep scanning if one ID is missing or offline.
        state.error = str(exc)
    if packet_errors:
        state.packet_error = "; ".join(packet_errors)
    return state


def parse_ids(args: argparse.Namespace) -> list[int]:
    if args.ids:
        return args.ids
    if args.model:
        return list(MODEL_IDS[args.model])
    return list(range(args.start_id, args.end_id + 1))


def format_state(state: ServoState) -> str:
    if state.error:
        return f"ID {state.servo_id:2d} | ERROR: {state.error}"
    line = (
        f"ID {state.servo_id:2d} | "
        f"torque={'ON ' if state.torque_enabled else 'OFF'} | "
        f"mode={state.operating_mode_name:<22} | "
        f"present_current_raw={state.present_current_raw:6d} | "
        f"goal_current_raw={state.goal_current_raw:6d} | "
        f"velocity_raw={state.present_velocity_raw:11d} | "
        f"position_raw={state.present_position_raw:11d} | "
        f"hardware_error=0x{state.hardware_error:02x}"
    )
    if state.hardware_error_flags:
        line += f" ({','.join(state.hardware_error_flags)})"
    if state.packet_error_code is not None:
        line += f" | packet_error_code=0x{state.packet_error_code:02x}"
    if state.packet_error:
        line += f" | packet_error={state.packet_error}"
    return line


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Dynamixel servo state without writing any registers."
    )
    parser.add_argument(
        "--port",
        required=True,
        help="Dynamixel serial device, for example /dev/ttyUSB1 or /dev/serial/by-id/...",
    )
    parser.add_argument("--baudrate", type=int, default=2_000_000)
    parser.add_argument(
        "--model",
        choices=sorted(MODEL_IDS),
        help="Use the JoyLo ID range: r1=0..15, r1pro=0..17.",
    )
    parser.add_argument(
        "--ids",
        nargs="+",
        type=int,
        help="Explicit servo IDs; overrides --model and --start-id/--end-id.",
    )
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--end-id", type=int, default=17)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object per servo instead of formatted text.",
    )
    return parser


def main() -> None:
    args = make_parser().parse_args()
    if args.model and args.ids:
        # Explicit IDs are allowed and intentionally take precedence.
        pass
    if not args.ids and not args.model and args.start_id > args.end_id:
        raise SystemExit("--start-id must not be greater than --end-id")

    ids = parse_ids(args)
    if not ids:
        raise SystemExit("No servo IDs specified")

    port_handler = PortHandler(args.port)
    packet_handler = PacketHandler(2.0)

    if not port_handler.openPort():
        raise SystemExit(f"Failed to open serial port: {args.port}")
    try:
        if not port_handler.setBaudRate(args.baudrate):
            raise SystemExit(f"Failed to set baudrate: {args.baudrate}")

        states = [read_servo_state(packet_handler, port_handler, servo_id) for servo_id in ids]
        if args.json:
            for state in states:
                print(json.dumps(asdict(state), ensure_ascii=False))
        else:
            print(f"Port: {args.port} | baudrate: {args.baudrate} | IDs: {ids}")
            print("Read-only diagnostic; no torque or operating-mode registers were written.")
            for state in states:
                print(format_state(state))
    finally:
        port_handler.closePort()


if __name__ == "__main__":
    main()
