"""Tradução de teclas físicas entre Windows (scan code) e Linux (keycode evdev).

As teclas viajam como tecla FÍSICA (código evdev, o mesmo do kernel Linux),
como o Deskflow faz: quem recebe aplica o próprio layout. Com o mesmo layout
nos dois PCs (ex.: ABNT2) sai exatamente o que foi digitado, e atalhos e
jogos funcionam porque a tecla é a mesma.

No X11, keycode X = keycode evdev + 8.
"""

# Scan codes do Windows com prefixo E0 (flag "extended") -> evdev
_WIN_EXT = {
    0x1C: 96,   # Enter do teclado numérico
    0x1D: 97,   # Ctrl direito
    0x35: 98,   # / numérico
    0x37: 99,   # Print Screen
    0x38: 100,  # Alt direito / AltGr
    0x47: 102, 0x48: 103, 0x49: 104,  # Home, Cima, PgUp
    0x4B: 105, 0x4D: 106,             # Esquerda, Direita
    0x4F: 107, 0x50: 108, 0x51: 109,  # End, Baixo, PgDn
    0x52: 110, 0x53: 111,             # Insert, Delete
    0x5B: 125, 0x5C: 126, 0x5D: 127,  # Win esq., Win dir., Menu
    0x20: 113, 0x2E: 114, 0x30: 115,  # Mudo, Volume -, Volume +
    0x22: 164, 0x24: 166,             # Play/Pause, Stop
    0x10: 165, 0x19: 163,             # Faixa anterior, próxima
    0x21: 140, 0x32: 172, 0x6C: 155,  # Calculadora, Página inicial, E-mail
    0x65: 217, 0x66: 156, 0x67: 173,  # Pesquisar, Favoritos, Atualizar
    0x6A: 158, 0x69: 159,             # Voltar, Avançar (navegador)
}

# Scan codes sem E0 a partir de 0x59 (abaixo disso o código é igual ao evdev)
_WIN_HIGH = {
    0x59: 117,  # = numérico
    0x64: 183, 0x65: 184, 0x66: 185, 0x67: 186, 0x68: 187, 0x69: 188,  # F13-F18
    0x6A: 189, 0x6B: 190, 0x6C: 191, 0x6D: 192, 0x6E: 193, 0x76: 194,  # F19-F24
    0x70: 93,   # Katakana/Hiragana
    0x73: 89,   # ABNT2: tecla /? ao lado do Shift direito
    0x79: 92, 0x7B: 94, 0x7D: 124,
    0x7E: 95,   # ABNT2: . do teclado numérico
}

# Teclas que o Windows trata de forma especial: vão por virtual key.
_VK_TO_EV = {0x13: 119, 0x90: 69, 0x2C: 99}  # Pause, Num Lock, Print Screen
EV_TO_VK = {ev: vk for vk, ev in _VK_TO_EV.items()}

_EV_TO_WIN = {ev: (sc, False) for sc, ev in _WIN_HIGH.items()}
_EV_TO_WIN.update({ev: (sc, True) for sc, ev in _WIN_EXT.items()})


def win_to_ev(scan, extended, vk):
    if vk in _VK_TO_EV:
        return _VK_TO_EV[vk]
    if not scan:
        return None
    if extended:
        return _WIN_EXT.get(scan)
    if scan < 0x59:
        return scan
    return _WIN_HIGH.get(scan)


def ev_to_win(ev):
    """evdev -> (scan code, extended) para SendInput, ou None."""
    if ev in _EV_TO_WIN:
        return _EV_TO_WIN[ev]
    if 0 < ev < 0x59:
        return ev, False
    return None
