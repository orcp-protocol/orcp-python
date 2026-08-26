"""Optional gamepad input for the ``orcp-drive`` teleop demo.

Wraps pygame's joystick module so the rest of the library never imports pygame
directly — ``orcp`` depends on pyserial alone, and a gamepad is a demo
convenience, not part of the client. Install with ``pip install "orcp[gamepad]"``.

Tested against a **PlayStation DualSense** over USB and Bluetooth on macOS. The
axis layout is SDL's standard game-controller mapping, so an Xbox or generic
pad presents the same way and should work unchanged.

⚠️ **A wireless pad is a link that can drop.** See :meth:`Gamepad.poll` — losing
the controller while the robot is moving is the failure mode that matters here,
and it is handled in two independent places.

⚠️ **A DualSense sleeps on its own.** It powered down mid-session during
development after a few minutes idle, and `pygame.joystick.get_count()` went to
0 with no warning. That is not an edge case to be defended against in theory —
it is the ordinary behaviour of the hardware, and it WILL happen to a user who
pauses to think. Wake it with the PS button. The disconnect handling exists
precisely because this is routine rather than exotic.
"""
from dataclasses import dataclass
from typing import Optional

# SDL standard game-controller axis map (verified on a DualSense, macOS).
AXIS_LEFT_X, AXIS_LEFT_Y = 0, 1
AXIS_RIGHT_X, AXIS_RIGHT_Y = 2, 3
AXIS_L2, AXIS_R2 = 4, 5          # triggers rest at -1.0, not 0.0

# DualSense / SDL button map.
BTN_CROSS, BTN_CIRCLE, BTN_SQUARE, BTN_TRIANGLE = 0, 1, 2, 3
BTN_PS, BTN_OPTIONS = 5, 6
BTN_L1, BTN_R1 = 9, 10

# ⚠️ Sticks do not rest at zero. A DualSense measured here rested at up to
# 0.027 on one axis — 2.7 % of full scale, which without a deadzone is a robot
# that creeps across the room while nobody is touching it.
DEADZONE = 0.10


@dataclass
class GamepadState:
    """One poll of the controller."""

    throttle: float = 0.0        # -1..+1, forward positive
    steer: float = 0.0           # -1..+1, left positive
    stop: bool = False           # Cross
    coast: bool = False          # Circle
    hold: bool = False           # Square
    reenable: bool = False       # Triangle
    toggle_preset: bool = False  # Options
    speed_up: bool = False       # R1
    speed_down: bool = False     # L1
    quit: bool = False           # PS
    disconnected: bool = False   # ⚠️ see poll()


def _apply_deadzone(value: float) -> float:
    """Zero small inputs, then rescale so travel stays smooth from the edge of
    the deadzone rather than jumping to it."""
    if abs(value) < DEADZONE:
        return 0.0
    scaled = (abs(value) - DEADZONE) / (1.0 - DEADZONE)
    return scaled if value > 0 else -scaled


def available() -> bool:
    """True if pygame is installed AND a controller is connected."""
    try:
        import pygame  # noqa: F401
    except ImportError:
        return False
    return Gamepad.count() > 0


class Gamepad:
    """A connected controller, polled once per teleop loop.

    Raises ImportError with an actionable message if pygame is missing, so the
    caller can fall back to keyboard rather than crashing.
    """

    def __init__(self, index: int = 0) -> None:
        try:
            import pygame
        except ImportError as exc:
            raise ImportError(
                "gamepad support needs pygame — install with: "
                'pip install "orcp[gamepad]"'
            ) from exc
        self._pygame = pygame

        # ⚠️ Force SDL's dummy video driver BEFORE init. pygame otherwise opens
        # a window, which on macOS steals focus from the terminal — and the
        # terminal is where curses is reading the keyboard. The symptom is a
        # teleop that stops responding to keys the moment a pad is plugged in.
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() <= index:
            raise RuntimeError("no gamepad connected")
        self._js = pygame.joystick.Joystick(index)
        self._js.init()
        self.name = self._js.get_name()
        self._connected = True
        self._prev_buttons = {}

    @staticmethod
    def count() -> int:
        try:
            import pygame
        except ImportError:
            return 0
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        pygame.joystick.init()
        return pygame.joystick.get_count()

    @property
    def connected(self) -> bool:
        return self._connected

    def _edge(self, idx: int) -> bool:
        """True on the press only, not while held — buttons are commands here,
        not throttles, and a held Square should not re-issue HOLD at 20 Hz."""
        now = bool(self._js.get_button(idx))
        was = self._prev_buttons.get(idx, False)
        self._prev_buttons[idx] = now
        return now and not was

    def poll(self) -> GamepadState:
        """Read the controller once.

        ⚠️ **Sets ``disconnected`` if the pad goes away**, and the caller MUST
        stop the robot when it does. A Bluetooth pad that drops, runs out of
        battery, or walks out of range leaves the robot driving at its last
        command otherwise — the same failure as a closed browser tab, and the
        reason the teleop also enables the controller's own stale-command
        timeout as an independent backstop.
        """
        pg = self._pygame
        st = GamepadState()

        for event in pg.event.get():
            if event.type == pg.JOYDEVICEREMOVED:
                self._connected = False
        if not self._connected:
            st.disconnected = True
            return st

        pg.event.pump()
        try:
            # Left stick Y is inverted: pushing forward reads negative.
            st.throttle = _apply_deadzone(-self._js.get_axis(AXIS_LEFT_Y))
            # Right stick X for steering — twin-stick. Mixing both axes of one
            # stick makes diagonal motion ambiguous and beginners fight it.
            # For single-stick, use AXIS_LEFT_X here instead.
            st.steer = _apply_deadzone(-self._js.get_axis(AXIS_RIGHT_X))

            st.stop = self._edge(BTN_CROSS)
            st.coast = self._edge(BTN_CIRCLE)
            st.hold = self._edge(BTN_SQUARE)
            st.reenable = self._edge(BTN_TRIANGLE)
            st.toggle_preset = self._edge(BTN_OPTIONS)
            st.speed_up = self._edge(BTN_R1)
            st.speed_down = self._edge(BTN_L1)
            st.quit = self._edge(BTN_PS)
        except Exception:
            # pygame raises if the device vanishes between the event check and
            # the read. Treat exactly as a disconnect.
            self._connected = False
            st = GamepadState(disconnected=True)
        return st

    def close(self) -> None:
        try:
            self._js.quit()
            self._pygame.joystick.quit()
            self._pygame.quit()
        except Exception:
            pass
