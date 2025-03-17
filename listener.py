import socket
import struct
import time

MCAST_GRP = "224.0.0.5"
INTERFACE_IP = "192.168.123.100"
RECEIVE_BUFFER_SIZE = 65535

def setup_socket(port):
    """ 멀티캐스트 소켓을 설정하고 특정 포트를 리스닝 """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**24)
    sock.bind(("", port))
    mreq = struct.pack("=4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton(INTERFACE_IP))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    
    return sock

def find_active_ips(port, timeout=3):
    """ 포트에서 활성화된 장치를 검색 """
    detected_ips = set()
    test_sock = setup_socket(port)
    test_sock.settimeout(timeout)

    try:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                _, addr = test_sock.recvfrom(RECEIVE_BUFFER_SIZE)
                detected_ips.add(addr[0])
            except socket.timeout:
                break
    finally:
        test_sock.close()

    return list(detected_ips)
