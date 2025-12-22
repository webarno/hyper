import pandas as pd
import numpy as np

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les features utilisées par le modèle ML
    à partir d'un DataFrame OHLC.
    """

    df = df.copy()

    # =====================
    # Range normalisé
    # =====================
    range_ = df["high"] - df["low"]
    df["range_norm"] = range_ / df["close"]

    # =====================
    # Position du close dans la bougie (safe)
    # =====================
    df["position_close"] = np.where(
        range_ == 0,
        0.5,
        (df["close"] - df["low"]) / range_
    )

    # =====================
    # Momentum
    # =====================
    df["momentum_3"] = df["close"].pct_change(3)

    # =====================
    # Moyenne de range
    # =====================
    df["range_norm_3"] = df["range_norm"].rolling(3).mean()

    # =====================
    # Delta de position du close
    # =====================
    df["close_pos_delta"] = df["position_close"].diff()

    # =====================
    # True Range
    # =====================
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # =====================
    # ATR & régime de volatilité
    # =====================
    atr_5 = tr.rolling(5).mean()
    atr_10 = tr.rolling(10).mean()

    df["ATR_5_pct"] = atr_5 / df["close"]
    df["volatility_regime"] = atr_5 / atr_10

    # =====================
    # Colonnes finales (ordre IMPORTANT)
    # =====================
    feature_cols = [
        "momentum_3",
        "range_norm_3",
        "close_pos_delta",
        "volatility_regime",
        "ATR_5_pct",
        "range_norm",
        "position_close",
        "open",
        "high",
        "low",
        "close",
    ]

    return df[feature_cols].dropna()
