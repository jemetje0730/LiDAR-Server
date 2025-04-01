from collections import deque
import math
import socket
import struct
import threading
import time

# 멀티캐스트 설정
MCAST_GRP = "224.0.0.5"
INTERFACE_IP = "192.168.0.100"
RECEIVE_BUFFER_SIZE = 65535
PACKET_SIZE = 809  # LiDAR 패킷 크기

# 최신 LiDAR 데이터 저장
latest_data = {"lidar": {0: [], 1: [], 2: [], 3: []}, "distances": {0: [], 1: [], 2: [], 3: []}}
last_update_time = time.time()
channel_history = deque(maxlen=10)
VERTICAL_ANGLES = [-1.07, 0, 1.07, 2]
stop_event = threading.Event()
sock_lidar = None
lidar_thread = None

# 패킷 조합을 위한 버퍼
buffer = bytearray()

def setup_socket(port):
    """ 멀티캐스트 소켓을 설정하고 특정 포트를 리스닝 """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**24)
    sock.bind(("", port))
    mreq = struct.pack("=4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton(INTERFACE_IP))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return sock

def bytes_to_distance(byte_pair):
    """ 2바이트 데이터를 거리값으로 변환 """
    return byte_pair[0] + byte_pair[1] * 0.01

def parse_data(data_bytes):
    global latest_data, last_update_time

    if len(data_bytes) != PACKET_SIZE:
        return

    command_high = data_bytes[4]
    channel_id = command_high & 0x03  # 상위 바이트 마지막 2비트 (0~3)

    if channel_id not in {0, 1, 2, 3}:
        return

    data = data_bytes[7:-2]  # 첫 7바이트와 마지막 2바이트 제외
    distances = [bytes_to_distance(data[i:i+2]) for i in range(0, len(data), 2)]

    if len(distances) < 400:
        return

    xyz_points = []
    angle = 0.0
    angle_step = 0.25
    vertical_radian = math.radians(VERTICAL_ANGLES[channel_id])

    for d in distances[:400]:
        radian = math.radians(angle)
        x = d * math.cos(radian) * math.cos(vertical_radian)
        y = d * math.sin(radian) * math.cos(vertical_radian)
        z = d * math.sin(vertical_radian)

        xyz_points.append({"x": x, "y": y, "z": z, "distance": d})
        angle += angle_step

    # 데이터 저장
    latest_data["lidar"][channel_id] = xyz_points
    latest_data["distances"][channel_id] = [{"angle": i * angle_step, "distance": d} for i, d in enumerate(distances[:400])]
    last_update_time = time.time()

def receive_and_process_data(sock, target_ip):
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(RECEIVE_BUFFER_SIZE)
            
            if addr[0] != target_ip:
                continue  # 특정 IP만 처리 
           
            parse_data(data)
        except Exception as e:
            print(f"❌ 오류: {e}")

def find_active_ips(port, timeout=3):
    """ 포트에서 활성화된 장치를 검색 """
    detected_ips = set()
    test_sock = setup_socket(port)
    test_sock.settimeout(timeout)

    print(f"🔍 포트 {port}에서 활성화된 장치를 찾는 중...")

    try:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data, addr = test_sock.recvfrom(RECEIVE_BUFFER_SIZE)
                detected_ips.add(addr[0])  # 중복 제거됨
            except socket.timeout:
                break
    finally:
        test_sock.close()

    return list(detected_ips)  # 모든 활성화된 IP 반환

def configure_lidar_listener(user_ip, user_port):
    """ 사용자가 입력한 IP/포트로 리스닝 시작 """
    global sock_lidar, lidar_thread, stop_event

    try:
        print(f"🔄 LiDAR 리스너 설정 변경: {user_ip}:{user_port}")

        # 기존 스레드 정리
        stop_event.set()
        if lidar_thread and lidar_thread.is_alive():
            lidar_thread.join()
        stop_event.clear()

        # 기존 소켓 닫기
        if sock_lidar:
            sock_lidar.close()

        # 새로운 소켓 설정
        sock_lidar = setup_socket(user_port)

        # 새로운 스레드 시작
        lidar_thread = threading.Thread(target=receive_and_process_data, args=(sock_lidar, user_ip), daemon=True)
        lidar_thread.start()

        print(f"✅ LiDAR 리스너가 {user_ip}:{user_port} 에서 실행 중")
        return True
    except Exception as e:
        print(f"❌ LiDAR 설정 실패: {str(e)}")
        return False

def get_latest_data():
    if time.time() - last_update_time > 2:
        return {"lidar_data": {0: [], 1: [], 2: [], 3: []}, "distances": {0: [], 1: [], 2: [], 3: []}}
    return latest_data