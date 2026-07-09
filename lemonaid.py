"""Lemonaid — simple pygame shooter."""
from __future__ import annotations

import math
import random
import sys
import threading
from pathlib import Path

import pygame

import updater

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

VERSION = "2.2.0"

ROOT = Path(__file__).parent
IMG = ROOT / "assets" / "images"
SND = ROOT / "assets" / "sounds"

WIDTH, HEIGHT = 960, 720
FPS = 60
BG = (29, 29, 29)
HUD_TOP = 88  # playfield starts below HUD; lemons bounce here


def load(name: str, size: tuple[int, int] | None = None) -> pygame.Surface:
    surf = pygame.image.load(str(IMG / name)).convert_alpha()
    if size:
        surf = pygame.transform.smoothscale(surf, size)
    return surf


def load_sound(name: str) -> pygame.mixer.Sound | None:
    path = SND / name
    if not path.exists():
        return None
    try:
        return pygame.mixer.Sound(str(path))
    except pygame.error:
        return None


def angle_to(from_pos, to_pos) -> float:
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - from_pos[1]
    return math.degrees(math.atan2(-dy, dx))  # 0 = right, CCW


def move(pos, angle_deg: float, speed: float) -> tuple[float, float]:
    rad = math.radians(angle_deg)
    return pos[0] + math.cos(rad) * speed, pos[1] - math.sin(rad) * speed


def blit_rotated(screen, image, pos, angle_deg: float):
    """Draw image centered at pos, rotated so image-right faces angle_deg."""
    rotated = pygame.transform.rotozoom(image, angle_deg, 1.0)
    rect = rotated.get_rect(center=(int(pos[0]), int(pos[1])))
    screen.blit(rotated, rect)
    return rect


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

class Player:
    def __init__(self, image: pygame.Surface):
        self.image = image
        self.x = WIDTH * 0.4
        self.y = HEIGHT * 0.65
        self.vx = 0.0
        self.vy = 0.0
        self.angle = 90.0
        self.cooldown = 0

    def update(self, keys, mouse_pos, mouse_down, shoot_cb):
        ax = (keys[pygame.K_d] - keys[pygame.K_a]) * 0.9
        ay = (keys[pygame.K_s] - keys[pygame.K_w]) * 0.9
        self.vx = (self.vx + ax) * 0.9
        self.vy = (self.vy + ay) * 0.9
        self.x += self.vx
        self.y += self.vy
        self.x = max(20, min(WIDTH - 20, self.x))
        self.y = max(HUD_TOP + 20, min(HEIGHT - 20, self.y))

        self.angle = angle_to((self.x, self.y), mouse_pos)

        if self.cooldown > 0:
            self.cooldown -= 1
        elif mouse_down or keys[pygame.K_SPACE]:
            shoot_cb(self.x, self.y, self.angle)
            self.cooldown = 4  # ~15 shots/sec at 60fps

    def draw(self, screen):
        blit_rotated(screen, self.image, (self.x, self.y), self.angle)


class Laser:
    def __init__(self, image: pygame.Surface, x: float, y: float, angle: float):
        self.image = image
        self.x = x
        self.y = y
        self.angle = angle
        self.alive = True

    def update(self):
        self.x, self.y = move((self.x, self.y), self.angle, 14)
        if self.x < -40 or self.x > WIDTH + 40 or self.y < -40 or self.y > HEIGHT + 40:
            self.alive = False

    def draw(self, screen):
        blit_rotated(screen, self.image, (self.x, self.y), self.angle)

    def rect(self) -> pygame.Rect:
        return pygame.Rect(self.x - 12, self.y - 6, 24, 12)


class Lemon:
    def __init__(self, image: pygame.Surface, x: float, y: float, angle: float, size: float, health: int, speed: float):
        self.base = image
        self.x = x
        self.y = y
        self.angle = angle  # movement direction
        self.spin = random.uniform(0, 360)
        self.size = size  # display scale relative to base
        self.health = health
        self.speed = speed
        self.alive = True
        self.flash = 0
        self.entered = False  # True once fully on-screen; then bounce at edges
        self._scaled: pygame.Surface | None = None
        self._scaled_key = 0.0
        self._rebuild()

    def _rebuild(self):
        key = round(self.size, 2)
        if self._scaled is not None and key == self._scaled_key:
            return
        w = max(8, int(self.base.get_width() * self.size))
        h = max(8, int(self.base.get_height() * self.size))
        self._scaled = pygame.transform.smoothscale(self.base, (w, h))
        self._scaled_key = key

    def update(self, shake):
        self.x += math.cos(math.radians(self.angle)) * self.speed + shake[0]
        self.y -= math.sin(math.radians(self.angle)) * self.speed - shake[1]
        self.spin += 5
        if self.flash > 0:
            self.flash -= 1

        r = self.radius()
        margin = r + 4
        top = HUD_TOP + margin

        # Wait until fully inside before enabling edge bounce
        if not self.entered:
            if margin <= self.x <= WIDTH - margin and top <= self.y <= HEIGHT - margin:
                self.entered = True
            return

        if self.x < margin:
            self.x = margin
            self.angle = 180 - self.angle
        elif self.x > WIDTH - margin:
            self.x = WIDTH - margin
            self.angle = 180 - self.angle
        if self.y < top:
            self.y = top
            self.angle = -self.angle
        elif self.y > HEIGHT - margin:
            self.y = HEIGHT - margin
            self.angle = -self.angle
        self.angle %= 360

    def hit(self) -> str:
        """Returns 'chip', 'split', or 'dead'."""
        if self.health > 0:
            self.health -= 1
            self.flash = 4
            return "chip"

        self.size *= 0.5
        self._rebuild()
        if self.size < 0.35:
            self.alive = False
            return "dead"

        self.speed *= 1.5
        self.angle += random.uniform(0, 180)
        return "split"

    def draw(self, screen):
        self._rebuild()
        img = self._scaled
        if self.flash > 0:
            img = img.copy()
            img.fill((180, 180, 180), special_flags=pygame.BLEND_RGB_ADD)
        blit_rotated(screen, img, (self.x, self.y), self.spin)

    def radius(self) -> float:
        return max(self._scaled.get_width(), self._scaled.get_height()) * 0.35


class Boom:
    def __init__(self, image: pygame.Surface, x: float, y: float, big: bool = False):
        self.image = image
        self.x = x
        self.y = y
        self.angle = random.uniform(0, 360)
        self.life = 12 if big else 8
        self.max_life = self.life
        self.big = big

    def update(self):
        self.life -= 1
        self.angle += 2

    @property
    def alive(self) -> bool:
        return self.life > 0

    def draw(self, screen):
        alpha = int(255 * self.life / self.max_life)
        img = self.image.copy()
        img.set_alpha(alpha)
        scale = 1.2 if self.big else 0.7
        w = max(8, int(img.get_width() * scale))
        h = max(8, int(img.get_height() * scale))
        img = pygame.transform.smoothscale(img, (w, h))
        blit_rotated(screen, img, (self.x, self.y), self.angle)


# Named wave presets — each wave has its own feel; intensity ramps 1 → 20
# weights = (big, medium, small); speed = spawn speed multiplier
WAVE_PRESETS = {
    # Phase 1–5: easy intro
    1:  {"name": "Ruhig",        "interval": 420, "weights": (1.0, 0.0, 0.0), "burst": 1, "speed": 0.75},
    2:  {"name": "Trampel",      "interval": 360, "weights": (0.7, 0.3, 0.0), "burst": 1, "speed": 0.85},
    3:  {"name": "Splitter",     "interval": 300, "weights": (0.35, 0.45, 0.2), "burst": 1, "speed": 0.95},
    4:  {"name": "Dicke Dinger", "interval": 340, "weights": (1.0, 0.0, 0.0), "burst": 1, "speed": 0.7},
    5:  {"name": "Aufwärmen",    "interval": 260, "weights": (0.45, 0.4, 0.15), "burst": 1, "speed": 1.0},
    # Phase 5–10: medium pressure
    6:  {"name": "Druck",        "interval": 200, "weights": (0.4, 0.45, 0.15), "burst": 2, "speed": 1.05},
    7:  {"name": "Hagel",        "interval": 150, "weights": (0.1, 0.3, 0.6), "burst": 2, "speed": 1.2},
    8:  {"name": "Kolosse",      "interval": 240, "weights": (0.9, 0.1, 0.0), "burst": 2, "speed": 0.85},
    9:  {"name": "Wirbel",       "interval": 130, "weights": (0.25, 0.4, 0.35), "burst": 2, "speed": 1.25},
    10: {"name": "Sturm",        "interval": 110, "weights": (0.3, 0.35, 0.35), "burst": 2, "speed": 1.15},
    # Phase 10–20: hard
    11: {"name": "Inferno",      "interval": 90,  "weights": (0.25, 0.35, 0.4), "burst": 3, "speed": 1.2},
    12: {"name": "Nadelregen",   "interval": 70,  "weights": (0.05, 0.25, 0.7), "burst": 3, "speed": 1.4},
    13: {"name": "Brocken",      "interval": 100, "weights": (0.75, 0.2, 0.05), "burst": 3, "speed": 0.95},
    14: {"name": "Blitz",        "interval": 65,  "weights": (0.15, 0.4, 0.45), "burst": 3, "speed": 1.45},
    15: {"name": "Orkan",        "interval": 60,  "weights": (0.3, 0.3, 0.4), "burst": 3, "speed": 1.3},
    16: {"name": "Lawine",       "interval": 55,  "weights": (0.4, 0.35, 0.25), "burst": 4, "speed": 1.25},
    17: {"name": "Wahnsinn",     "interval": 50,  "weights": (0.2, 0.35, 0.45), "burst": 4, "speed": 1.4},
    18: {"name": "Überfall",     "interval": 45,  "weights": (0.15, 0.3, 0.55), "burst": 4, "speed": 1.5},
    19: {"name": "Apokalypse",   "interval": 42,  "weights": (0.35, 0.35, 0.3), "burst": 4, "speed": 1.35},
    20: {"name": "Finale",       "interval": 38,  "weights": (0.25, 0.35, 0.4), "burst": 5, "speed": 1.55},
}

NORMAL = {"name": "Normal", "interval": 220, "weights": (0.45, 0.35, 0.2), "burst": 1, "speed": 1.0}
PEAK = {"name": "Hochphase", "interval": 40, "weights": (0.25, 0.35, 0.4), "burst": 4, "speed": 1.4}
TWO_MIN = 120 * FPS
BANNER_LEN = 150   # total frames the wave title is shown
BANNER_FADE = 40   # fade-in and fade-out each last this many frames


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------

class Game:
    def __init__(self):
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        pygame.display.set_caption("Lemonaid")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()

        self.img_ship = load("ship.png", (72, 70))
        self.img_laser = load("laser.png", (48, 16))
        self.img_lemon = load("lemon.png", (90, 68))
        self.img_flash = load("flash.png", (120, 120))
        self.img_rays = load("rays.png", (280, 280))
        self.font = pygame.font.SysFont("segoeui", 40, bold=True)
        self.font_title = pygame.font.SysFont("segoeui", 84, bold=True)
        self.font_small = pygame.font.SysFont("segoeui", 26)
        self.font_wave = pygame.font.SysFont("segoeui", 28, bold=True)

        self.snd_laser = load_sound("laser.wav")
        self.snd_hit = load_sound("hit.wav")
        self.snd_split = load_sound("split.wav")
        self.snd_big = load_sound("boom.wav")
        self.music = load_sound("music.mp3")

        self.player = Player(self.img_ship)
        self.lasers: list[Laser] = []
        self.lemons: list[Lemon] = []
        self.booms: list[Boom] = []
        self.score = 0
        self.shake = [0.0, 0.0]
        self.spawn_timer = 120
        self.phase_index = 0
        self.phase_timer = 0
        self.phase_label = "Welle 1"
        self.phase_cfg = WAVE_PRESETS[1]
        self.wave_banner = 0
        self.wave_banner_max = BANNER_LEN
        self.fade_t = 0.0  # 0..1 during fade-down phase
        self.running = True
        self.state = "start"  # start | play | pause
        self.title_pulse = 0
        self.menu_btn = pygame.Rect(0, 0, 200, 52)
        self.menu_btn.bottomright = (WIDTH - 24, HEIGHT - 24)

        # Update check (only at launch / start menu — never mid-game)
        self.update_info: dict | None = None
        self.update_dismissed = False
        self.update_busy = False
        self.update_error = ""
        self.update_popup = pygame.Rect(0, 0, 520, 280)
        self.update_popup.center = (WIDTH // 2, HEIGHT // 2)
        self.btn_update = pygame.Rect(0, 0, 200, 48)
        self.btn_later = pygame.Rect(0, 0, 160, 48)
        self.btn_update.midbottom = (self.update_popup.centerx - 110, self.update_popup.bottom - 28)
        self.btn_later.midbottom = (self.update_popup.centerx + 110, self.update_popup.bottom - 28)
        updater.check_async(VERSION, self._on_update_check)

        # Schedule of phases. kind: wave_range | normal | peak | fade
        self.schedule = [
            {"kind": "wave_range", "start": 1, "end": 5},
            {"kind": "normal", "duration": TWO_MIN, "label": "Normal"},
            {"kind": "wave_range", "start": 5, "end": 10},
            {"kind": "normal", "duration": TWO_MIN, "label": "Normal"},
            {"kind": "wave_range", "start": 10, "end": 20},
            {"kind": "peak", "duration": TWO_MIN, "label": "Hochphase"},
            {"kind": "fade", "duration": TWO_MIN, "label": "Abklingen"},
            {"kind": "normal", "duration": TWO_MIN, "label": "Normal"},
            {"kind": "loop_to", "index": 0},  # back to waves 1-5
        ]

    def music_start(self):
        if self.music and not self.music.get_num_channels():
            self.music.play(loops=-1)

    def music_stop(self):
        if self.music:
            self.music.stop()

    def _on_update_check(self, info: dict | None):
        # Called from background thread — only store result; UI reads it on start screen
        self.update_info = info

    def update_popup_visible(self) -> bool:
        return (
            self.state == "start"
            and self.update_info is not None
            and not self.update_dismissed
        )

    def begin_update(self):
        if not self.update_info or self.update_busy:
            return
        self.update_busy = True
        self.update_error = ""

        def worker():
            try:
                updater.apply_and_restart(self.update_info)
                pygame.event.post(pygame.event.Event(pygame.QUIT))
            except Exception as exc:
                self.update_error = str(exc)[:120]
                self.update_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def play(self, sound):
        if sound:
            sound.play()

    def shoot(self, x, y, angle):
        if len(self.lasers) >= 24:
            return
        self.lasers.append(Laser(self.img_laser, x, y, angle))
        self.play(self.snd_laser)

    def count_big_lemons(self) -> int:
        return sum(1 for lemon in self.lemons if lemon.alive and lemon.size >= 1.8)

    def begin_phase(self, index: int):
        self.phase_index = index % len(self.schedule)
        step = self.schedule[self.phase_index]

        if step["kind"] == "loop_to":
            self.begin_phase(step["index"])
            return

        self.fade_t = 0.0
        self.spawn_timer = 60
        self.wave_banner = BANNER_LEN
        self.wave_banner_max = BANNER_LEN

        if step["kind"] == "wave_range":
            self.sub_wave = step["start"]
            self.sub_wave_end = step["end"]
            # Whole range lasts 2 minutes, split evenly across numbered waves
            count = step["end"] - step["start"] + 1
            self.wave_len = max(1, TWO_MIN // count)
            self._apply_numbered_wave(self.sub_wave)
            self.phase_timer = self.wave_len
        elif step["kind"] == "normal":
            self.phase_cfg = dict(NORMAL)
            self.phase_label = step["label"]
            self.phase_timer = step["duration"]
            self.sub_wave = None
        elif step["kind"] == "peak":
            self.phase_cfg = dict(PEAK)
            self.phase_label = step["label"]
            self.phase_timer = step["duration"]
            self.sub_wave = None
        elif step["kind"] == "fade":
            self.phase_cfg = dict(PEAK)
            self.phase_label = step["label"]
            self.phase_timer = step["duration"]
            self.sub_wave = None

    def _apply_numbered_wave(self, number: int):
        cfg = WAVE_PRESETS[number]
        self.phase_cfg = dict(cfg)
        self.phase_label = f"Welle {number}: {cfg['name']}"
        self.sub_wave = number

    def advance_phase(self):
        step = self.schedule[self.phase_index]
        # Inside a wave range: go to next numbered wave, or leave the range
        if step["kind"] == "wave_range" and self.sub_wave is not None:
            if self.sub_wave < self.sub_wave_end:
                self._apply_numbered_wave(self.sub_wave + 1)
                self.phase_timer = self.wave_len
                self.wave_banner = BANNER_LEN
                self.wave_banner_max = BANNER_LEN
                self.spawn_timer = 45
                return
        self.begin_phase(self.phase_index + 1)

    def active_spawn_cfg(self) -> dict:
        cfg = dict(self.phase_cfg)
        step = self.schedule[self.phase_index]
        if step["kind"] == "fade":
            # lerp from peak → normal over the fade duration
            t = self.fade_t
            cfg["interval"] = int(PEAK["interval"] + (NORMAL["interval"] - PEAK["interval"]) * t)
            cfg["burst"] = max(1, int(round(PEAK["burst"] + (NORMAL["burst"] - PEAK["burst"]) * t)))
            cfg["speed"] = PEAK["speed"] + (NORMAL["speed"] - PEAK["speed"]) * t
            # blend weights
            pw, nw = PEAK["weights"], NORMAL["weights"]
            cfg["weights"] = tuple(pw[i] + (nw[i] - pw[i]) * t for i in range(3))
            cfg["name"] = "Abklingen"
        return cfg

    def pick_size(self) -> tuple[float, int]:
        cfg = self.active_spawn_cfg()
        big_w, med_w, small_w = cfg["weights"]
        step = self.schedule[self.phase_index]

        # During numbered waves, trust the wave's mix so each wave feels distinct.
        # Outside waves, unlock sizes gradually by score.
        if step["kind"] != "wave_range":
            if self.score < 100:
                med_w = 0.0
                small_w = 0.0
            elif self.score < 500:
                small_w = 0.0

        if self.count_big_lemons() >= 3:
            big_w = 0.0

        total = big_w + med_w + small_w
        if total <= 0:
            if med_w > 0 or (step["kind"] != "wave_range" and self.score >= 100):
                return 1.1, 4
            return 2.2, 8

        roll = random.random() * total
        if roll < big_w:
            return 2.2, 8
        if roll < big_w + med_w:
            return 1.1, 4
        return 0.55, 2

    def spawn_lemon(self):
        if len(self.lemons) >= 200:
            return

        size, health = self.pick_size()
        if size >= 1.8 and self.count_big_lemons() >= 3:
            cfg = self.active_spawn_cfg()
            _, med_w, small_w = cfg["weights"]
            if small_w > 0 and random.random() < 0.45:
                size, health = 0.55, 2
            elif med_w > 0:
                size, health = 1.1, 4
            else:
                return

        side = random.choice(("left", "right", "bottom"))
        pad = 80 + size * 50
        y_min = HUD_TOP + 40
        if side == "left":
            x, y = -pad, random.uniform(y_min, HEIGHT - 80)
        elif side == "right":
            x, y = WIDTH + pad, random.uniform(y_min, HEIGHT - 80)
        else:
            x, y = random.uniform(80, WIDTH - 80), HEIGHT + pad

        target = (
            random.uniform(WIDTH * 0.25, WIDTH * 0.75),
            random.uniform(HUD_TOP + 40, HEIGHT * 0.75),
        )
        angle = angle_to((x, y), target)
        speed_mul = float(self.active_spawn_cfg().get("speed", 1.0))
        speed = (0.8 + min(0.35, self.score / 800)) * (1.15 if size < 1.5 else 1.0) * speed_mul
        self.lemons.append(
            Lemon(self.img_lemon, x, y, angle, size=size, health=health, speed=speed)
        )

    def spawn_interval(self) -> int:
        return max(35, int(self.active_spawn_cfg()["interval"]))

    def split_lemon(self, lemon: Lemon):
        for offset in (0, 120):
            if len(self.lemons) >= 200:
                break
            child = Lemon(
                self.img_lemon,
                lemon.x,
                lemon.y,
                lemon.angle + offset + random.uniform(0, 40),
                size=lemon.size,
                health=0,
                speed=lemon.speed,
            )
            child.entered = True
            self.lemons.append(child)

    def trigger_shake(self, angle: float):
        self.shake[0] = math.cos(math.radians(angle)) * 10
        self.shake[1] = -math.sin(math.radians(angle)) * 10

    def handle_hits(self):
        for laser in self.lasers:
            if not laser.alive:
                continue
            for lemon in self.lemons:
                if not lemon.alive:
                    continue
                dx = laser.x - lemon.x
                dy = laser.y - lemon.y
                if dx * dx + dy * dy > (lemon.radius() + 10) ** 2:
                    continue

                laser.alive = False
                result = lemon.hit()
                if result == "chip":
                    self.score += 1
                    self.play(self.snd_hit)
                elif result == "dead":
                    self.score += 1
                    self.booms.append(Boom(self.img_flash, lemon.x, lemon.y, big=False))
                    self.play(self.snd_split)
                elif result == "split":
                    big = lemon.size > 1.0
                    self.score += 6 if big else 1
                    self.booms.append(
                        Boom(self.img_rays if big else self.img_flash, lemon.x, lemon.y, big=big)
                    )
                    self.play(self.snd_big if big else self.snd_split)
                    if big:
                        lemon.health = 2
                        self.trigger_shake(self.player.angle)
                    self.split_lemon(lemon)
                break

    def update(self):
        keys = pygame.key.get_pressed()
        mouse = pygame.mouse.get_pos()
        mouse_down = pygame.mouse.get_pressed()[0]

        self.player.update(keys, mouse, mouse_down, self.shoot)

        for laser in self.lasers:
            laser.update()
        for lemon in self.lemons:
            lemon.update(self.shake)
        for boom in self.booms:
            boom.update()

        self.handle_hits()

        self.lasers = [o for o in self.lasers if o.alive]
        self.lemons = [o for o in self.lemons if o.alive]
        self.booms = [o for o in self.booms if o.alive]

        # decay shake
        self.shake[0] *= 0.85
        self.shake[1] *= 0.85
        if abs(self.shake[0]) < 0.05:
            self.shake[0] = 0.0
        if abs(self.shake[1]) < 0.05:
            self.shake[1] = 0.0

        # phases / waves
        if self.wave_banner > 0:
            self.wave_banner -= 1

        step = self.schedule[self.phase_index]
        if step["kind"] == "fade":
            # 0 at start of fade → 1 at end
            dur = max(1, step["duration"])
            self.fade_t = 1.0 - (self.phase_timer / dur)
            self.fade_t = max(0.0, min(1.0, self.fade_t))

        self.phase_timer -= 1
        if self.phase_timer <= 0:
            self.advance_phase()

        self.spawn_timer -= 1
        if self.spawn_timer <= 0:
            burst = int(self.active_spawn_cfg()["burst"])
            for _ in range(burst):
                self.spawn_lemon()
            self.spawn_timer = self.spawn_interval()

    def start_game(self):
        self.state = "play"
        self.player = Player(self.img_ship)
        self.lasers.clear()
        self.lemons.clear()
        self.booms.clear()
        self.score = 0
        self.shake = [0.0, 0.0]
        self.begin_phase(0)
        self.music_start()

    def go_to_start(self):
        self.state = "start"
        self.lasers.clear()
        self.lemons.clear()
        self.booms.clear()
        self.score = 0
        self.shake = [0.0, 0.0]
        self.wave_banner = 0
        self.music_stop()

    def set_pause(self, paused: bool):
        if paused:
            self.state = "pause"
            self.music_stop()
        else:
            self.state = "play"
            self.music_start()

    def draw_fancy_text(self, text, font, color, glow_color, center, glow=True):
        label = font.render(text, True, color)
        rect = label.get_rect(center=center)
        if glow:
            glow_surf = font.render(text, True, glow_color)
            glow_surf.set_alpha(55)
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
                self.screen.blit(glow_surf, rect.move(dx, dy))
        outline = font.render(text, True, (25, 18, 0))
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            self.screen.blit(outline, rect.move(dx, dy))
        self.screen.blit(label, rect)
        return rect

    def draw_start(self):
        self.screen.fill(BG)
        self.title_pulse += 0.05

        self.draw_fancy_text(
            "LEMONAID",
            self.font_title,
            (255, 245, 160),
            (255, 200, 40),
            (WIDTH // 2, 70),
        )

        lemon = pygame.transform.rotozoom(self.img_lemon, self.title_pulse * 20, 2.4)
        lemon_rect = lemon.get_rect(center=(WIDTH // 2, HEIGHT * 0.42))
        self.screen.blit(lemon, lemon_rect)

        if int(self.title_pulse * 2) % 2 == 0:
            self.draw_fancy_text(
                "Leertaste oder Klick zum Starten",
                self.font_small,
                (255, 255, 255),
                (255, 220, 100),
                (WIDTH // 2, HEIGHT * 0.72),
                glow=False,
            )

        hint = self.font_small.render(
            "WASD bewegen  ·  Maus zielen  ·  Klick / Leertaste schießen",
            True,
            (170, 170, 170),
        )
        self.screen.blit(hint, hint.get_rect(center=(WIDTH // 2, HEIGHT * 0.82)))

        ver = self.font_small.render(f"v{VERSION}", True, (120, 120, 120))
        self.screen.blit(ver, ver.get_rect(bottomleft=(16, HEIGHT - 14)))

        if self.update_popup_visible():
            self.draw_update_popup()

        pygame.display.flip()

    def draw_update_popup(self):
        info = self.update_info or {}
        # Dim background
        dim = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        self.screen.blit(dim, (0, 0))

        box = self.update_popup
        pygame.draw.rect(self.screen, (40, 40, 44), box, border_radius=16)
        pygame.draw.rect(self.screen, (255, 210, 70), box, width=2, border_radius=16)

        title = self.font_wave.render("Update verfügbar", True, (255, 245, 160))
        self.screen.blit(title, title.get_rect(midtop=(box.centerx, box.top + 22)))

        remote = str(info.get("version", "?"))
        body = self.font_small.render(
            f"Version {remote} ist da  (aktuell {VERSION})",
            True,
            (230, 230, 230),
        )
        self.screen.blit(body, body.get_rect(midtop=(box.centerx, box.top + 70)))

        notes = str(info.get("notes", "")).strip()
        if notes:
            note_surf = self.font_small.render(notes[:70], True, (180, 180, 180))
            self.screen.blit(note_surf, note_surf.get_rect(midtop=(box.centerx, box.top + 110)))

        if self.update_busy:
            status = self.font_small.render("Update wird geladen…", True, (255, 210, 70))
            self.screen.blit(status, status.get_rect(center=(box.centerx, box.centery + 20)))
            return

        if self.update_error:
            err = self.font_small.render(self.update_error, True, (255, 120, 100))
            self.screen.blit(err, err.get_rect(midtop=(box.centerx, box.top + 150)))

        mouse = pygame.mouse.get_pos()
        for btn, label, active in (
            (self.btn_update, "Jetzt updaten", True),
            (self.btn_later, "Später", False),
        ):
            hover = btn.collidepoint(mouse)
            if active:
                fill = (255, 210, 60) if hover else (255, 190, 40)
                text_c = (30, 22, 0)
            else:
                fill = (70, 70, 76) if hover else (55, 55, 60)
                text_c = (230, 230, 230)
            pygame.draw.rect(self.screen, fill, btn, border_radius=10)
            pygame.draw.rect(self.screen, (255, 245, 160) if active else (100, 100, 110), btn, width=2, border_radius=10)
            txt = self.font_small.render(label, True, text_c)
            self.screen.blit(txt, txt.get_rect(center=btn.center))

    def phase_side_info(self) -> tuple[list[str], list[str]]:
        """Return (left_lines, right_lines) for HUD beside the score."""
        step = self.schedule[self.phase_index]
        cfg = self.active_spawn_cfg()
        secs = max(0, self.phase_timer // FPS)

        if step["kind"] == "wave_range" and self.sub_wave is not None:
            left = [f"Welle {self.sub_wave}/{self.sub_wave_end}"]
            name = self.phase_cfg.get("name", "")
            if name:
                left.append(name)
        else:
            left = [self.phase_label]
            name = cfg.get("name", "")
            if name and name != self.phase_label:
                left.append(name)

        right = [f"Noch {secs // 60}:{secs % 60:02d}", self._next_phase_hint()]
        return left, right

    def _next_phase_hint(self) -> str:
        step = self.schedule[self.phase_index]
        if step["kind"] == "wave_range" and self.sub_wave is not None:
            if self.sub_wave < self.sub_wave_end:
                return f"Nächste: Welle {self.sub_wave + 1}"
            nxt_i = (self.phase_index + 1) % len(self.schedule)
            nxt = self.schedule[nxt_i]
            if nxt["kind"] == "loop_to":
                nxt = self.schedule[nxt["index"]]
            return f"Nächste: {self._step_name(nxt)}"
        nxt_i = (self.phase_index + 1) % len(self.schedule)
        nxt = self.schedule[nxt_i]
        if nxt["kind"] == "loop_to":
            nxt = self.schedule[nxt["index"]]
        return f"Nächste: {self._step_name(nxt)}"

    def _step_name(self, step: dict) -> str:
        if step["kind"] == "wave_range":
            return f"Wellen {step['start']}–{step['end']}"
        return step.get("label", step["kind"])

    def draw_score(self):
        text = f"Punkte: {self.score}"
        label = self.font.render(text, True, (255, 245, 170))
        rect = label.get_rect(midtop=(WIDTH // 2, 8))

        glow = self.font.render(text, True, (255, 210, 70))
        glow.set_alpha(50)
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            self.screen.blit(glow, rect.move(dx, dy))

        outline = self.font.render(text, True, (30, 22, 0))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.screen.blit(outline, rect.move(dx, dy))

        self.screen.blit(label, rect)

        # Wave infos: two lines, pinned to left/right edges so nothing clips
        left_lines, right_lines = self.phase_side_info()
        color = (190, 195, 210)
        margin = 14
        line_h = self.font_small.get_height() + 2
        for i, line in enumerate(left_lines):
            surf = self.font_small.render(line, True, color)
            self.screen.blit(surf, surf.get_rect(topleft=(margin, 8 + i * line_h)))
        for i, line in enumerate(right_lines):
            surf = self.font_small.render(line, True, color)
            self.screen.blit(surf, surf.get_rect(topright=(WIDTH - margin, 8 + i * line_h)))

        # Thin bar under HUD — lemons bounce off this line
        pygame.draw.line(self.screen, (70, 72, 82), (0, HUD_TOP), (WIDTH, HUD_TOP), 2)
        pygame.draw.line(self.screen, (45, 46, 52), (0, HUD_TOP + 2), (WIDTH, HUD_TOP + 2), 1)

        if self.wave_banner > 0:
            # Symmetric fade-in / fade-out
            age = self.wave_banner_max - self.wave_banner  # 0 at start
            if age < BANNER_FADE:
                alpha = int(255 * age / BANNER_FADE)
            elif self.wave_banner < BANNER_FADE:
                alpha = int(255 * self.wave_banner / BANNER_FADE)
            else:
                alpha = 255
            title = self.phase_label.split(":")[0]
            banner = self.font_title.render(title, True, (255, 245, 160))
            banner.set_alpha(alpha)
            subtitle = self.phase_cfg.get("name", "")
            name = self.font_wave.render(subtitle, True, (255, 255, 255))
            name.set_alpha(alpha)
            self.screen.blit(banner, banner.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 30)))
            if subtitle and subtitle not in title:
                self.screen.blit(name, name.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 40)))

    def draw_world(self):
        """Draw the playfield onto self.screen (no flip)."""
        self.screen.fill(BG)
        ox, oy = self.shake

        for group in (self.booms, self.lemons, self.lasers):
            for obj in group:
                obj.x += ox
                obj.y += oy
                obj.draw(self.screen)
                obj.x -= ox
                obj.y -= oy

        self.player.x += ox
        self.player.y += oy
        self.player.draw(self.screen)
        self.player.x -= ox
        self.player.y -= oy
        self.draw_score()

    def to_grayscale(self, surface: pygame.Surface) -> pygame.Surface:
        gray = surface.copy()
        arr = pygame.surfarray.pixels3d(gray)
        avg = arr.mean(axis=2, keepdims=True)
        arr[:] = avg
        del arr
        return gray

    def draw_menu_button(self):
        mouse = pygame.mouse.get_pos()
        hover = self.menu_btn.collidepoint(mouse)
        fill = (255, 210, 60) if hover else (255, 190, 40)
        border = (255, 245, 160)
        pygame.draw.rect(self.screen, fill, self.menu_btn, border_radius=12)
        pygame.draw.rect(self.screen, border, self.menu_btn, width=2, border_radius=12)
        label = self.font_small.render("Zum Start", True, (30, 22, 0))
        self.screen.blit(label, label.get_rect(center=self.menu_btn.center))

    def draw_pause(self):
        self.draw_world()
        # freeze look: whole scene black & white
        gray = self.to_grayscale(self.screen)
        self.screen.blit(gray, (0, 0))

        # pause title (also gray-ish white)
        pause = self.font_title.render("PAUSE", True, (230, 230, 230))
        self.screen.blit(pause, pause.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 20)))
        hint = self.font_small.render("Esc zum Fortsetzen", True, (180, 180, 180))
        self.screen.blit(hint, hint.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 40)))

        # colored button stays on top
        self.draw_menu_button()
        pygame.display.flip()

    def draw(self):
        self.draw_world()
        pygame.display.flip()

    def run(self):
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if self.update_popup_visible() and not self.update_busy:
                            self.update_dismissed = True
                        elif self.state == "play":
                            self.set_pause(True)
                        elif self.state == "pause":
                            self.set_pause(False)
                        else:
                            self.running = False
                    elif self.state == "start" and event.key in (pygame.K_SPACE, pygame.K_RETURN):
                        if self.update_popup_visible():
                            if not self.update_busy:
                                self.begin_update()
                        else:
                            self.start_game()
                    elif self.state == "pause" and event.key in (pygame.K_SPACE, pygame.K_RETURN):
                        self.set_pause(False)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.state == "start":
                        if self.update_popup_visible():
                            if self.update_busy:
                                pass
                            elif self.btn_update.collidepoint(event.pos):
                                self.begin_update()
                            elif self.btn_later.collidepoint(event.pos):
                                self.update_dismissed = True
                        else:
                            self.start_game()
                    elif self.state == "pause":
                        if self.menu_btn.collidepoint(event.pos):
                            self.go_to_start()
                        else:
                            self.set_pause(False)

            if self.state == "start":
                self.draw_start()
            elif self.state == "pause":
                self.draw_pause()
            else:
                self.update()
                self.draw()
            self.clock.tick(FPS)

        pygame.quit()
        if self.update_busy:
            # Apply script already spawned — exit cleanly so files can be replaced
            sys.exit(0)


if __name__ == "__main__":
    Game().run()
