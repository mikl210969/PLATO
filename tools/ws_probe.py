import asyncio, json, sys, websockets

MODE = sys.argv[1] if len(sys.argv) > 1 else "subscribe"
STREAMS = ["solusdt@depth20@100ms", "btcusdt@aggTrade", "btcusdt@depth@100ms"]

if MODE == "subscribe":
    URL = "wss://stream.binance.com:9443/ws"
else:  # combined
    URL = "wss://stream.binance.com:9443/stream?streams=" + "/".join(STREAMS)

async def main():
    print(f"Режим: {MODE} -> {URL}")
    async with websockets.connect(URL, ping_interval=20, ping_timeout=10) as ws:
        if MODE == "subscribe":
            await ws.send(json.dumps({"method": "SUBSCRIBE", "params": STREAMS, "id": 1}))
            print("SUBSCRIBE отправлен...")
        try:
            for i in range(8):
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                print(f"[{i}] {msg[:110]}")
        except asyncio.TimeoutError:
            print("!!! 10 СЕКУНД ТИШИНЫ — данные не приходят")

asyncio.run(main())