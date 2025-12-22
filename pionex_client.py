# pionex_client.py
import time
import requests
import pandas as pd


class PionexClient:
    BASE_URL = "https://api.pionex.com"

    def get_klines(self, symbol: str, interval: str = "5M", limit: int = 100, end_time_ms: int | None = None) -> pd.DataFrame:
        """
        Pionex: GET /api/v1/market/klines
        interval: "1M","5M","15M","30M","60M","4H","8H","12H","1D"
        endTime: millisecond timestamp (optionnel mais on le met pour éviter les retours vides)
        """
        if end_time_ms is None:
            end_time_ms = int(time.time() * 1000)

        url = f"{self.BASE_URL}/api/v1/market/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": int(limit),
            "endTime": int(end_time_ms),
        }

        r = requests.get(url, params=params, timeout=10)
        res = r.json()

        if not res.get("result", False):
            raise Exception(f"Pionex error: {res}")

        klines = res.get("data", {}).get("klines", [])
        if not klines:
            # On lève une erreur explicite pour éviter df vide + iloc[-1]
            raise Exception(f"Pionex: klines vides pour {symbol} interval={interval} (réponse ok mais data vide)")

        # klines = liste de dicts: {time, open, close, high, low, volume}
        df = pd.DataFrame(klines)

        # Normaliser les colonnes
        if "time" in df.columns:
            df = df.rename(columns={"time": "timestamp"})

        # Types
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)

        # Trier au cas où
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
