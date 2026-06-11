"""Procedural pixel-art weapon icons (12x10), cached per key."""
import pygame

_KEY = (255, 0, 255)
_cache = {}

_OUT = (30, 18, 22)
_METAL = (168, 174, 186)
_DARKM = (108, 114, 126)
_WOODC = (150, 104, 58)


def _flask(s, liquid):
    pygame.draw.rect(s, _METAL, (5, 0, 2, 2))                # cork
    pygame.draw.polygon(s, (190, 215, 220),
                        [(5, 2), (6, 2), (9, 6), (9, 9), (2, 9), (2, 6)])
    pygame.draw.polygon(s, liquid, [(3, 6), (8, 6), (8, 9), (3, 9)])


def _rocket(s, col):
    pygame.draw.polygon(s, col, [(1, 7), (8, 7), (8, 4), (1, 4)])
    pygame.draw.polygon(s, _METAL, [(8, 3), (11, 5), (8, 8)])  # nose
    pygame.draw.polygon(s, (255, 180, 60), [(0, 3), (2, 5), (0, 7)])


def _bomb(s, col):
    pygame.draw.circle(s, col, (5, 6), 4)
    pygame.draw.line(s, _METAL, (7, 3), (9, 1))
    s.set_at((10, 0), (255, 220, 100))


def weapon_icon(key):
    if key in _cache:
        return _cache[key]
    s = pygame.Surface((12, 10))
    s.fill(_KEY)
    s.set_colorkey(_KEY)

    if key == "bazooka":
        _rocket(s, (210, 70, 50))
    elif key == "grenade":
        _bomb(s, (90, 170, 80))
        pygame.draw.rect(s, _METAL, (4, 1, 3, 2))
    elif key == "cluster":
        _bomb(s, (220, 200, 90))
        s.set_at((2, 9), (220, 200, 90)); s.set_at((5, 9), (220, 200, 90))
        s.set_at((8, 9), (220, 200, 90))
    elif key == "shotgun":
        pygame.draw.rect(s, _DARKM, (0, 4, 9, 2))
        pygame.draw.rect(s, _WOODC, (7, 5, 5, 3))
        s.set_at((0, 3), (255, 230, 140))
    elif key == "hammer":
        pygame.draw.rect(s, _METAL, (3, 1, 6, 4))
        pygame.draw.rect(s, _WOODC, (5, 5, 2, 5))
    elif key == "mine":
        pygame.draw.circle(s, (90, 95, 105), (6, 6), 3)
        for dx, dy in ((0, -4), (-3, -2), (3, -2), (-4, 1), (4, 1)):
            s.set_at((6 + dx, 6 + dy), _METAL)
        s.set_at((6, 5), (255, 70, 70))
    elif key == "dynamite":
        pygame.draw.rect(s, (200, 60, 50), (4, 2, 4, 8))
        pygame.draw.line(s, (230, 210, 160), (6, 1), (8, 0))
        s.set_at((9, 0), (255, 230, 110))
    elif key == "airstrike":
        pygame.draw.polygon(s, _METAL, [(1, 3), (10, 3), (7, 5), (4, 5)])
        pygame.draw.rect(s, _METAL, (5, 1, 2, 3))
        for x in (3, 6, 9):
            s.set_at((x, 8), (255, 170, 60))
    elif key == "homing":
        pygame.draw.circle(s, (255, 90, 90), (3, 5), 3, 1)
        s.set_at((3, 5), (255, 90, 90))
        _rocket(s, (230, 90, 160))
    elif key == "melon":
        pygame.draw.circle(s, (110, 200, 80), (6, 5), 4)
        pygame.draw.arc(s, (60, 130, 60), (2, 1, 9, 9), 1.2, 2.6, 2)
        s.set_at((6, 1), (90, 60, 40))
    elif key == "acid":
        _flask(s, (120, 235, 60))
    elif key == "oil":
        _flask(s, (60, 50, 44))
    elif key == "sludge":
        _flask(s, (110, 150, 50))
    elif key == "slime":
        _flask(s, (230, 120, 190))
    elif key == "lavabomb":
        _bomb(s, (70, 60, 70))
        pygame.draw.circle(s, (255, 130, 40), (5, 6), 2)
    elif key == "gas":
        pygame.draw.rect(s, (140, 150, 90), (3, 2, 6, 8))
        pygame.draw.rect(s, _METAL, (5, 0, 2, 2))
        s.set_at((10, 2), (170, 180, 110)); s.set_at((11, 1), (170, 180, 110))
    elif key == "steam":
        _bomb(s, (170, 180, 190))
        s.set_at((2, 1), (220, 225, 235)); s.set_at((1, 3), (220, 225, 235))
    elif key == "powder":
        pygame.draw.polygon(s, (200, 70, 70), [(2, 9), (9, 9), (6, 4)])
        s.set_at((6, 2), (255, 230, 110)); s.set_at((5, 3), (255, 200, 90))
    elif key == "crystal":
        pygame.draw.polygon(s, (120, 200, 255), [(5, 0), (8, 4), (6, 9),
                                                 (3, 5)])
        pygame.draw.line(s, (200, 235, 255), (5, 1), (5, 7))
    elif key == "water":
        pygame.draw.rect(s, _DARKM, (0, 4, 6, 3))
        for i, x in enumerate(range(6, 12)):
            s.set_at((x, 4 + (i % 2)), (90, 160, 230))
    elif key == "freeze":
        for a, b in (((6, 0), (6, 9)), ((1, 2), (11, 7)), ((11, 2), (1, 7))):
            pygame.draw.line(s, (160, 220, 255), a, b)
    elif key == "spark":
        pygame.draw.lines(s, (255, 240, 120), False,
                          [(7, 0), (4, 4), (7, 5), (4, 9)], 2)
    elif key == "lightning":
        pygame.draw.rect(s, (90, 90, 110), (1, 0, 10, 3))
        pygame.draw.lines(s, (255, 240, 140), False,
                          [(6, 3), (4, 6), (7, 6), (5, 9)], 1)
    elif key == "blackhole":
        pygame.draw.circle(s, (20, 8, 30), (6, 5), 4)
        pygame.draw.circle(s, (170, 90, 255), (6, 5), 4, 1)
        s.set_at((6, 5), (240, 220, 255))
    elif key == "transmute":
        pygame.draw.line(s, _WOODC, (2, 9), (8, 3), 2)
        s.set_at((9, 2), (255, 230, 140))
        s.set_at((11, 0), (190, 120, 255)); s.set_at((10, 3), (190, 120, 255))
    elif key == "liquefy":
        pygame.draw.rect(s, (130, 126, 134), (3, 0, 6, 4))
        for x in (3, 5, 7):
            pygame.draw.line(s, (130, 126, 134), (x, 4), (x, 7 + (x % 3)))
    elif key == "gravflip":
        pygame.draw.polygon(s, (200, 200, 220), [(3, 3), (5, 0), (7, 3)])
        pygame.draw.polygon(s, (255, 170, 60), [(5, 6), (7, 9), (9, 6)])
        pygame.draw.line(s, (200, 200, 220), (5, 2), (5, 5))
        pygame.draw.line(s, (255, 170, 60), (7, 4), (7, 7))
    elif key == "rope":
        pygame.draw.arc(s, (220, 200, 140), (2, 0, 8, 8), 0.5, 3.6, 1)
        pygame.draw.polygon(s, _METAL, [(8, 6), (10, 8), (8, 9)])
    elif key == "jetpack":
        pygame.draw.rect(s, _METAL, (3, 1, 3, 6))
        pygame.draw.rect(s, _DARKM, (7, 1, 3, 6))
        s.set_at((4, 8), (255, 170, 60)); s.set_at((8, 8), (255, 170, 60))
        s.set_at((4, 9), (255, 220, 110)); s.set_at((8, 9), (255, 220, 110))
    elif key == "chute":
        pygame.draw.arc(s, (220, 90, 80), (1, 0, 10, 8), 0.0, 3.14, 2)
        pygame.draw.line(s, (200, 200, 200), (2, 4), (6, 9))
        pygame.draw.line(s, (200, 200, 200), (10, 4), (6, 9))
    elif key == "teleport":
        pygame.draw.circle(s, (120, 200, 255), (6, 5), 4, 1)
        pygame.draw.circle(s, (200, 235, 255), (6, 5), 2, 1)
    elif key == "girder":
        pygame.draw.rect(s, _METAL, (0, 4, 12, 3))
        for x in range(1, 12, 3):
            s.set_at((x, 5), _DARKM)
    elif key == "torch":
        pygame.draw.rect(s, _DARKM, (1, 4, 6, 3))
        pygame.draw.polygon(s, (255, 170, 60), [(7, 3), (11, 5), (7, 8)])
        s.set_at((8, 5), (255, 235, 140))
    elif key == "drill":
        pygame.draw.rect(s, _DARKM, (4, 0, 4, 4))
        pygame.draw.polygon(s, _METAL, [(4, 4), (8, 4), (6, 9)])
        pygame.draw.line(s, _DARKM, (5, 5), (7, 5))
    elif key == "napalm":
        pygame.draw.polygon(s, _METAL, [(1, 2), (10, 2), (7, 4), (4, 4)])
        for x in (2, 5, 8):
            pygame.draw.line(s, (255, 140, 40), (x, 6), (x, 9))
    else:
        pygame.draw.circle(s, (200, 200, 210), (6, 5), 3)

    _cache[key] = s
    return s
