import socket
import struct
import threading
import math
import time

# 기본 설정
MCAST_GRP_LIDAR = "224.0.0.5"
MCAST_PORT_LIDAR = 5000
MCAST_GRP_CLOUD_POINT = "224.0.0.6"
MCAST_PORT_CLOUD_POINT = 5000
INTERFACE_IP = "192.168.123.100"

PACKET_SIZE = 2169
RECEIVE_BUFFER_SIZE = 65535

# 최신 데이터 저장 (캐싱)
latest_data = {
    "lidar": [],
    "cloud_point": []
}
last_update_time = {
    "lidar": time.time(),
    "cloud_point": time.time()
}

# ✅ 기존 스레드 종료 플래그 추가
stop_event = threading.Event()
lidar_thread = None
sock_lidar = None


def setup_socket(multicast_group, port, interface_ip):
    """ 멀티캐스트 소켓을 설정하는 함수 """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**24)

    # ✅ 멀티캐스트 인터페이스 설정 (기본값 유지)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))

    # ✅ 모든 네트워크 인터페이스에서 포트 수신 가능
    sock.bind(("", port))  # ❌ 특정 IP 바인드 대신 "" 사용

    # ✅ 멀티캐스트 그룹 가입
    mreq = struct.pack("=4s4s", socket.inet_aton(multicast_group), socket.inet_aton(interface_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    return sock



def bytes_to_distance(byte_pair):
    """ 2바이트 데이터를 거리값으로 변환 """
    distance_d = byte_pair[0]  # 정수 부분
    distance_f = byte_pair[1] * 0.01  # 소수 부분
    return distance_d + distance_f


def parse_data(data_bytes, data_type):
    """ 데이터를 파싱하여 XYZ 좌표 변환 후 저장 """
    global latest_data, last_update_time

    # ✅ 데이터 길이 검증 추가
    if len(data_bytes) < 9:  # 최소 길이 검증 (헤더(7) + 데이터(최소 2) = 9)
        print(f"⚠️ {data_type.capitalize()} 데이터 오류: 패킷 길이 부족 ({len(data_bytes)}), 무시")
        return

    data = data_bytes[7:-2]  # ✅ 헤더(7)와 CRC(2) 제거

    if len(data) < 2:  # ✅ 거리 데이터가 최소 2바이트 이상이어야 함
        print(f"⚠️ {data_type.capitalize()} 데이터 오류: 유효한 거리 데이터 없음")
        return

    if len(data) % 2 != 0:  # ✅ 거리 데이터가 2바이트 단위가 아닐 경우
        print(f"⚠️ {data_type.capitalize()} 데이터 오류: 데이터 길이가 올바르지 않음 ({len(data)})")
        return

    distances = []
    for i in range(0, len(data), 2):
        try:
            distances.append(bytes_to_distance(data[i:i+2]))
        except IndexError:
            print(f"⚠️ {data_type.capitalize()} 데이터 오류: 인덱스 초과 ({i}/{len(data)})")
            return  # ✅ 오류 발생 시 무시하고 리턴

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



def receive_and_process_data(sock, data_type):
    """ 데이터 수신 및 처리 (버퍼를 이용하여 패킷 조합) """
    global last_received_source
    buffer = bytearray()  # ✅ 패킷 조립을 위한 버퍼 추가

    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(RECEIVE_BUFFER_SIZE)
            last_received_source = addr  # ✅ 최신 수신 IP 저장

            buffer.extend(data)  # ✅ 버퍼에 데이터 추가

            # ✅ 버퍼가 PACKET_SIZE 이상이면 처리
            while len(buffer) >= PACKET_SIZE:
                packet_data = buffer[:PACKET_SIZE]  # PACKET_SIZE만큼 데이터 추출
                buffer = buffer[PACKET_SIZE:]  # 남은 데이터 유지

                if len(packet_data) != PACKET_SIZE:
                    print(f"⚠️ {data_type.capitalize()} 데이터 오류: 패킷 크기 불일치 ({len(packet_data)}), 무시")
                    buffer.clear()  # ✅ 잘못된 데이터는 삭제
                    continue  

                parse_data(packet_data, data_type)  # ✅ 올바른 크기의 데이터만 파싱

            time.sleep(0.01)  # ✅ CPU 과부하 방지

        except Exception as e:
            if stop_event.is_set():
                break  # 종료 요청이 있으면 오류 무시하고 루프 종료
            print(f"❌ {data_type.capitalize()} 데이터 수신 오류: {e}")


def configure_lidar_listener(user_ip, user_port):
    """
    사용자가 입력한 IP/포트로 LiDAR 소켓을 설정하고 데이터 수신 시작
    """
    global sock_lidar, lidar_thread, stop_event

    try:
        print(f"🔄 LiDAR 소켓 설정 변경: {user_ip}:{user_port}")

        # ✅ 기존 스레드 종료 요청
        stop_event.set()
        if lidar_thread and lidar_thread.is_alive():
            lidar_thread.join()  # 기존 스레드가 완전히 종료될 때까지 대기
        stop_event.clear()  # ✅ 새로운 스레드를 위해 플래그 초기화

        # ✅ 기존 소켓 닫기
        if sock_lidar:
            sock_lidar.close()

        # ✅ 새 소켓 설정 (user_ip 대신 INTERFACE_IP 사용)
        sock_lidar = setup_socket(MCAST_GRP_LIDAR, user_port, INTERFACE_IP)

        # ✅ 새로운 스레드 시작
        lidar_thread = threading.Thread(target=receive_and_process_data, args=(sock_lidar, "lidar"), daemon=True)
        lidar_thread.start()

        print(f"✅ LiDAR 리스너가 {user_ip}:{user_port} 에서 실행 중")
        return True
    except Exception as e:
        print(f"❌ LiDAR 설정 실패: {str(e)}")
        return False


def get_last_received_source():
    """ 가장 최근 LiDAR 데이터가 수신된 IP/포트 반환 """
    return last_received_source


def start_listener():
    """ LiDAR 및 Cloud Point 수신을 별도 스레드에서 실행 """
    global lidar_thread, sock_lidar, sock_cloud_point

    sock_lidar = setup_socket(MCAST_GRP_LIDAR, MCAST_PORT_LIDAR, INTERFACE_IP)
    sock_cloud_point = setup_socket(MCAST_GRP_CLOUD_POINT, MCAST_PORT_CLOUD_POINT, INTERFACE_IP)

    lidar_thread = threading.Thread(target=receive_and_process_data, args=(sock_lidar, "lidar"), daemon=True)
    cloud_point_thread = threading.Thread(target=receive_and_process_data, args=(sock_cloud_point, "cloud_point"), daemon=True)

    lidar_thread.start()
    cloud_point_thread.start()


def get_latest_data():
    """ 최신 데이터 반환 (2초 이상 업데이트 없으면 빈 값) """
    return {
        "lidar_data": latest_data["lidar"] if time.time() - last_update_time["lidar"] <= 2 else [],
        "cloud_point_data": latest_data["cloud_point"] if time.time() - last_update_time["cloud_point"] <= 2 else []
    }
