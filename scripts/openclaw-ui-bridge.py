#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import signal
import socket
import socketserver
import subprocess
import threading


LISTEN_HOST = os.environ.get("OPENCLAW_UI_BRIDGE_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("OPENCLAW_UI_BRIDGE_PORT", "38889"))
TARGET_HOST = os.environ.get("OPENCLAW_UI_TARGET_HOST", "127.0.0.1")
TARGET_PORT = int(os.environ.get("OPENCLAW_UI_TARGET_PORT", "18789"))

UPSTREAM_COMMAND = [
    "openshell",
    "doctor",
    "exec",
    "--",
    "kubectl",
    "-n",
    "openshell",
    "exec",
    "-i",
    "nemoclaw-main",
    "--",
    "nc",
    TARGET_HOST,
    str(TARGET_PORT),
]


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class BridgeHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        proc = subprocess.Popen(
            UPSTREAM_COMMAND,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

        assert proc.stdin is not None
        assert proc.stdout is not None

        def client_to_ssh() -> None:
            try:
                while True:
                    data = self.request.recv(65536)
                    if not data:
                        break
                    proc.stdin.write(data)
                    proc.stdin.flush()
            except OSError:
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def ssh_to_client() -> None:
            try:
                while True:
                    data = proc.stdout.read(65536)
                    if not data:
                        break
                    self.request.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    self.request.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        upstream = threading.Thread(target=client_to_ssh, daemon=True)
        downstream = threading.Thread(target=ssh_to_client, daemon=True)
        upstream.start()
        downstream.start()
        upstream.join()
        downstream.join()

        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    server = ThreadedTCPServer((LISTEN_HOST, LISTEN_PORT), BridgeHandler)

    def shutdown(_signum: int, _frame: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"openclaw-ui-bridge listening on {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
