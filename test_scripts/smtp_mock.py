#!/usr/bin/env python3
"""
Minimal SMTP mock server for testing. Listens on localhost:1025 by default.
Prints received emails to stdout. Use with sending workflows: set SMTP host to
127.0.0.1 and port to 1025 (no auth).
"""
import socket
import sys
import threading

HOST = "127.0.0.1"
PORT = 1025


def handle_client(conn, addr):
    try:
        conn.sendall(b"220 localhost ESMTP Mock Server\r\n")
        buf = b""
        in_data = False
        msg_lines = []

        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\r\n" in buf or (in_data and b"\n" in buf):
                if b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    line = line.decode("utf-8", errors="replace")
                else:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode("utf-8", errors="replace").rstrip("\r")

                if in_data:
                    if line.strip() == ".":
                        in_data = False
                        full_msg = "\n".join(msg_lines)
                        print("--- Received email ---", flush=True)
                        print(full_msg, flush=True)
                        print("--- End ---", flush=True)
                        conn.sendall(b"250 OK Message accepted\r\n")
                        msg_lines = []
                        continue
                    if line.startswith(".."):
                        line = line[1:]
                    msg_lines.append(line)
                    continue

                cmd = line.upper().split()[0] if line.split() else ""
                if cmd in ("HELO", "EHLO"):
                    conn.sendall(b"250 OK\r\n")
                elif cmd == "MAIL":
                    conn.sendall(b"250 OK\r\n")
                elif cmd == "RCPT":
                    conn.sendall(b"250 OK\r\n")
                elif cmd == "DATA":
                    conn.sendall(b"354 Go ahead\r\n")
                    in_data = True
                elif cmd == "QUIT":
                    conn.sendall(b"221 Bye\r\n")
                    return
                else:
                    conn.sendall(b"250 OK\r\n")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
    finally:
        conn.close()


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)
    print(f"Mock SMTP server on {HOST}:{PORT} (Ctrl+C to stop)", file=sys.stderr)
    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr))
        t.daemon = True
        t.start()


if __name__ == "__main__":
    main()