"""91kds / 悠兔 `com.jiaoxiang.Cipher` 的 enCipher/deCipher 重实现。

逆向自 libcipher.so:
  Java_com_jiaoxiang_Cipher_deCipher / enCipher, 0x42a9c / 0x4314c
参数为 0,0 时固定 p=149, q=83:
  n = 12367, phi = 12136, e = 3, d = 3^-1 mod phi = 8091

格式:
  enCipher: 明文 -> base64 -> 每个字符 c 变成 pow(c,3,n)+n，空格分隔的十进制串
  deCipher: 十进制串 -> pow(v-n,8091,n) 还原字符 -> base64 解码 -> 明文
"""

import base64
import re
import sys

P, Q = 149, 83
N = P * Q
PHI = (P - 1) * (Q - 1)
E = 3
D = pow(E, -1, PHI)  # 8091


def en_cipher(data: bytes) -> str:
    b64 = base64.b64encode(data)
    return " ".join(str(pow(c, E, N) + N) for c in b64)


def de_cipher(text: str) -> bytes:
    if not re.fullmatch(r"[\d\s]*", text):
        raise ValueError("不是 deCipher 的密文格式（应为空格分隔的十进制数）")
    vals = [int(x) for x in text.split()]
    mid = bytes(pow((v - N) % N, D, N) for v in vals)
    return base64.b64decode(mid)


def _selftest() -> None:
    sample = b"var a=1;function f(){return 'x';}"
    enc = en_cipher(sample)
    assert de_cipher(enc) == sample
    assert de_cipher(en_cipher(b"")) == b""
    print(f"selftest ok  n={N} phi={PHI} e={E} d={D}")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    if cmd == "--selftest":
        _selftest()
        return 0
    if cmd == "enc":
        data = open(argv[2], "rb").read() if len(argv) > 2 else sys.stdin.buffer.read()
        print(en_cipher(data))
        return 0
    if cmd == "dec":
        text = open(argv[2], "r", encoding="utf-8").read() if len(argv) > 2 else sys.stdin.read()
        sys.stdout.buffer.write(de_cipher(text))
        return 0
    print("usage: kds_cipher.py {enc|dec|--selftest} [file]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
