def analyze_build(cpu_socket, motherboard_socket):
    if not cpu_socket or not motherboard_socket:
        return {
            'level': 'info',
            'message': 'Wybierz procesor i plyte glowna, aby sprawdzic socket.',
        }

    if cpu_socket != motherboard_socket:
        return {
            'level': 'blocking',
            'message': f'Procesor wymaga socketu {cpu_socket}, a plyta ma {motherboard_socket}.',
        }

    return {
        'level': 'ok',
        'message': f'Socket {cpu_socket} procesora i plyty glownej jest zgodny.',
    }
