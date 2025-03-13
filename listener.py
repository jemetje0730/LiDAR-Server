import socket
import struct
import threading
import time
import math

MCAST_GRP = "224.0.0.5"  # 멀티캐스트 그룹
INTERFACE_IP = "192.168.123.100"  # 내 컴퓨터의 IP
RECEIVE_BUFFER_SIZE = 65535
PACKET_SIZE = 2169  # 최종적으로 필요한 패킷 크기

latest_data = {"lidar": []}
last_received_source = ("", 0)
last_update_time = {"lidar": time.time()}

stop_event = threading.Event()
lidar_thread = None
sock_lidar = None

# ✅ 패킷 조합을 위한 버퍼
buffer = bytearray()

def setup_socket(port):
    """ 멀티캐스트 소켓을 설정하고 특정 포트를 리스닝 """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**24)
    sock.bind(("", port))  # 모든 네트워크 인터페이스에서 수신 가능
    mreq = struct.pack("=4s4s", socket.inet_aton(MCAST_GRP), socket.inet_aton(INTERFACE_IP))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return sock

def bytes_to_distance(byte_pair):
    """ 2바이트 데이터를 거리값으로 변환 """
    distance_d = byte_pair[0]  # 정수 부분
    distance_f = byte_pair[1] * 0.01  # 소수 부분
    return distance_d + distance_f

def parse_data(data_bytes):
    """ 데이터를 파싱하여 XYZ 좌표 변환 후 저장 """
    global latest_data, last_update_time

    if len(data_bytes) != PACKET_SIZE:
        print(f"⚠️ 데이터 오류: 패킷 길이 불완전 ({len(data_bytes)})")
        return

    data = data_bytes[7:-2]  # 헤더(7)와 CRC(2) 제거

    if len(data) < 2 or len(data) % 2 != 0:
        print("⚠️ 데이터 오류: 유효한 거리 데이터 없음")
        return

    distances = [bytes_to_distance(data[i:i+2]) for i in range(0, len(data), 2)]

    angle = 0.0
    angle_step = 0.25
    xyz_points = []

    for distance in distances:
        radian = math.radians(angle - 135)
        x, y, z = distance * math.cos(radian), distance * math.sin(radian), 0
        xyz_points.append({
            "x": -y,
            "y": x,
            "z": z,
            "distance": math.sqrt(x**2 + y**2),
        })
        angle += angle_step

    latest_data["lidar"] = xyz_points
    last_update_time["lidar"] = time.time()

def receive_and_process_data(sock, target_ip):
    """ 멀티캐스트 그룹에서 특정 IP에서 오는 데이터만 처리 """
    global last_received_source, buffer

    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(RECEIVE_BUFFER_SIZE)

            if addr[0] == target_ip:
                last_received_source = addr

                # ✅ 패킷을 버퍼에 추가하여 조합
                buffer.extend(data)

                # ✅ 버퍼가 2169바이트 이상이면 패킷을 처리
                if len(buffer) >= PACKET_SIZE:
                    full_packet = buffer[:PACKET_SIZE]
                    buffer = buffer[PACKET_SIZE:]  # 남은 데이터 유지
                    parse_data(full_packet)  # 완성된 패킷 파싱
        except Exception as e:
            print(f"❌ 데이터 수신 오류: {e}")

def find_active_ip(port, timeout=3):
    """
    특정 포트에서 실제로 데이터를 보내고 있는 IP를 찾음
    """
    test_sock = setup_socket(port)
    test_sock.settimeout(timeout)
    start_time = time.time()

    print(f"🔍 포트 {port}에서 활성화된 장치를 찾는 중...")

    try:
        while time.time() - start_time < timeout:
            data, addr = test_sock.recvfrom(RECEIVE_BUFFER_SIZE)
            print(f"📡 감지된 장치: {addr[0]}:{addr[1]}")
            return addr[0]  # 데이터를 보내는 장치의 IP 반환
    except socket.timeout:
        print("❌ 활성화된 장치를 찾을 수 없음")
        return None
    finally:
        test_sock.close()

def configure_lidar_listener(user_ip, user_port):
    """ 사용자가 입력한 IP/포트로 리스닝 시작 """
    global sock_lidar, lidar_thread, stop_event

    try:
        print(f"🔄 LiDAR 리스너 설정 변경: {user_ip}:{user_port}")

        # 이전 스레드 종료
        stop_event.set()
        if lidar_thread and lidar_thread.is_alive():
            lidar_thread.join()
        stop_event.clear()

        # 이전 소켓 닫기
        if sock_lidar:
            sock_lidar.close()

        # 새로운 소켓 설정
        sock_lidar = setup_socket(user_port)

        # 새로운 스레드 시작 / 해당 IP에서 오는 데이터만 수신
        lidar_thread = threading.Thread(target=receive_and_process_data, args=(sock_lidar, user_ip), daemon=True)
        lidar_thread.start()

        print(f"✅ LiDAR 리스너가 {user_ip}:{user_port} 에서 실행 중")
        return True
    except Exception as e:
        print(f"❌ LiDAR 설정 실패: {str(e)}")
        return False

def get_latest_data():
    """ 최신 데이터 반환 """
    return {
        "lidar_data": latest_data["lidar"] if time.time() - last_update_time["lidar"] <= 2 else []
    }

