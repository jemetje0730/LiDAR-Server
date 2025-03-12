import queue
import socket
import struct
import threading
import math
import time

MCAST_GRP_LIDAR = "224.0.0.5"
MCAST_PORT_LIDAR = 5000
MCAST_GRP_CLOUD_POINT = "224.0.0.6"
MCAST_PORT_CLOUD_POINT = 5000
INTERFACE_IP = "192.168.123.100"

PACKET_SIZE = 2169  # ✅ 공통 패킷 크기 사용
RECEIVE_BUFFER_SIZE = 65535  # ✅ 수신 버퍼 크기 조정

# ✅ 최신 데이터 저장 (캐싱)
latest_data = {
    "lidar": [],
    "cloud_point": []
}
last_update_time = {
    "lidar": time.time(),
    "cloud_point": time.time()
}

# ✅ 소켓 설정 함수
def setup_socket(multicast_group, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**24)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(INTERFACE_IP))
    sock.bind(("", port))

    mreq = struct.pack("=4s4s", socket.inet_aton(multicast_group), socket.inet_aton(INTERFACE_IP))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    return sock

sock_lidar = setup_socket(MCAST_GRP_LIDAR, MCAST_PORT_LIDAR)
sock_cloud_point = setup_socket(MCAST_GRP_CLOUD_POINT, MCAST_PORT_CLOUD_POINT)


def bytes_to_distance(byte_pair):
    """ 2바이트 데이터를 거리값으로 변환 (이상값 방지) """
    distance_d = byte_pair[0]  # 정수 부분
    distance_f = byte_pair[1] * 0.01  # 소수 부분
    return distance_d + distance_f


def parse_data(data_bytes, data_type):
    """ 데이터를 파싱하여 XYZ 좌표 변환 후 저장 (이상값 필터링 추가) """
    global latest_data, last_update_time

    data = data_bytes[7:-2]  # ✅ 헤더(7)와 CRC(2) 제거
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
            "distance": math.sqrt(x**2 + y**2),  # 계산된 거리
        })
        angle += angle_step

    latest_data[data_type] = xyz_points
    last_update_time[data_type] = time.time()


last_received_source = ("", 0)  # 최신 수신 IP/포트 저장

def receive_and_process_data(sock, data_type):
    """ 데이터 수신 및 처리 """
    global last_received_source
    buffer = bytearray()

    while True:
        try:
            data, addr = sock.recvfrom(RECEIVE_BUFFER_SIZE)  # ✅ 수신한 IP/포트 확인
            last_received_source = addr  # ✅ 가장 최근 데이터 수신 IP 저장

            buffer.extend(data)

            while len(buffer) >= PACKET_SIZE:
                packet_data = buffer[:PACKET_SIZE]
                buffer = buffer[PACKET_SIZE:]

                if len(packet_data) != PACKET_SIZE:
                    print(f"⚠️ Invalid {data_type} packet size ({len(packet_data)}), discarding buffer...")
                    buffer.clear()
                    continue  # 문제 있는 패킷은 무시

                parse_data(packet_data, data_type)

            time.sleep(0.01)  # ✅ CPU 과부하 방지

        except Exception as e:
            print(f"❌ {data_type.capitalize()} 데이터 수신 오류: {e}")
            buffer.clear()

def get_last_received_source():
    """ 가장 최근 LiDAR 데이터가 수신된 IP/포트 반환 """
    return last_received_source

def start_listener():
    """ LiDAR 및 Cloud Point 수신을 별도 스레드에서 실행 """
    threading.Thread(target=receive_and_process_data, args=(sock_lidar, "lidar"), daemon=True).start()
    threading.Thread(target=receive_and_process_data, args=(sock_cloud_point, "cloud_point"), daemon=True).start()

def get_latest_data():
    """ 최신 데이터 반환 (2초 이상 업데이트 없으면 빈 값) """
    return {
        "lidar_data": latest_data["lidar"] if time.time() - last_update_time["lidar"] <= 2 else [],
        "cloud_point_data": latest_data["cloud_point"] if time.time() - last_update_time["cloud_point"] <= 2 else []
    }
