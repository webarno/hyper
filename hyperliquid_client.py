# hyperliquid_client.py
import os
import math
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from dotenv import load_dotenv
from eth_account import Account

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


class HyperliquidClient:
    """
    Client minimal Hyperliquid (perps):
    - open_long(notional_usdc)
    - close_position(coin)
    - has_position(coin)
    - set_isolated_leverage(coin, leverage)
    - set_tp_sl_for_position(coin, tp_pct, sl_pct)  -> 2 trigger orders reduce-only
    """

    def __init__(self, slippage: float = 0.01, skip_ws: bool = True):
        load_dotenv()

        priv = os.getenv("HYPERLIQUID_PRIVATE_KEY") or os.getenv("HYPERLIQUID_SECRET_KEY")
        if not priv:
            raise Exception("❌ .env: HYPERLIQUID_PRIVATE_KEY manquante")
        if not priv.startswith("0x"):
            priv = "0x" + priv

        self.account = Account.from_key(priv)
        self.address = self.account.address

        # Si tu trades directement avec cette wallet -> OK
        # Si tu utilises un agent/api wallet pour trader un compte principal -> mets HYPERLIQUID_ACCOUNT_ADDRESS
        self.account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS") or self.address

        base_url = os.getenv("HYPERLIQUID_API_URL") or constants.MAINNET_API_URL

        self.info = Info(base_url, skip_ws=skip_ws)
        self.exchange = Exchange(self.account, base_url, account_address=self.account_address)

        self.slippage = float(slippage)

        self._meta = None
        self._sz_decimals_cache = {}

    # -------------------------
    # META / PRECISIONS
    # -------------------------
    def _load_meta(self):
        if self._meta is None:
            self._meta = self.info.meta()
        return self._meta

    def _get_sz_decimals(self, coin: str) -> int:
        if coin in self._sz_decimals_cache:
            return self._sz_decimals_cache[coin]

        meta = self._load_meta()
        for item in meta.get("universe", []):
            if item.get("name") == coin:
                d = int(item.get("szDecimals"))
                self._sz_decimals_cache[coin] = d
                return d

        raise ValueError(f"Coin introuvable dans meta (perps): {coin}")

    def _round_size(self, coin: str, raw_sz: float) -> float:
        """
        Arrondi SZ au szDecimals (lot size) pour éviter float_to_wire rounding.
        """
        d = self._get_sz_decimals(coin)
        step = Decimal("1").scaleb(-d)  # 10^-d
        sz_dec = Decimal(str(raw_sz)).quantize(step, rounding=ROUND_DOWN)

        if sz_dec <= 0:
            sz_dec = step

        sz_f = float(format(sz_dec, "f"))
        sz_f = float(f"{sz_f:.{d}f}")
        return sz_f

    def _price_step_sigfigs(self, px: float) -> Decimal:
        """
        Hyperliquid: prix avec ~5 significant figures (et au plus 6 décimales).
        On calcule un pas = 10^(floor(log10(px)) - 4), borné à 1e-6 mini.
        """
        if px <= 0:
            return Decimal("0.000001")

        exp = int(math.floor(math.log10(px)))  # ex: 225 -> 2
        step = Decimal("1").scaleb(exp - 4)    # 10^(exp-4) => garde 5 sig figs
        min_step = Decimal("0.000001")         # max 6 decimals
        if step < min_step:
            step = min_step
        return step

    def _round_price(self, px: float, rounding: str = "down") -> float:
        """
        Arrondi prix à un pas valide (5 sig figs, <= 6 décimales).
        rounding: "down" ou "up"
        """
        step = self._price_step_sigfigs(float(px))
        dpx = Decimal(str(px))
        if rounding == "up":
            q = dpx.quantize(step, rounding=ROUND_UP)
        else:
            q = dpx.quantize(step, rounding=ROUND_DOWN)

        # float propre
        return float(format(q, "f"))

    # -------------------------
    # MARKET DATA
    # -------------------------
    def get_mid_price(self, coin: str) -> float:
        mids = self.info.all_mids()
        px = mids.get(coin)
        if px is None:
            raise ValueError(f"Pas de mid price pour {coin}")
        return float(px)

    # -------------------------
    # POSITIONS
    # -------------------------
    def has_position(self, coin: str):
        """
        Retourne (True, position_dict) si szi != 0.
        """
        state = self.info.user_state(self.account_address)
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin") == coin:
                szi = float(pos.get("szi", 0))
                if abs(szi) > 0:
                    return True, pos
        return False, None

    # -------------------------
    # RISK: LEVERAGE ISOLÉ
    # -------------------------
    def set_isolated_leverage(self, coin: str, leverage: int):
        """
        is_cross=False => isolé.
        """
        try:
            resp = self.exchange.update_leverage(leverage, coin, is_cross=False)
            return resp
        except TypeError:
            # certaines versions du SDK ont l'ordre des args différent
            resp = self.exchange.update_leverage(leverage, coin, False)
            return resp

    # -------------------------
    # ORDERS
    # -------------------------
    def open_long(self, coin: str, notional_usdc: float):
        """
        Ouvre un long au marché (implémenté via limite agressive par le SDK).
        notional_usdc = valeur notionnelle (ex: SIZE_USDC * LEVERAGE si tu veux).
        """
        mid = self.get_mid_price(coin)
        raw_sz = float(notional_usdc) / mid
        sz = self._round_size(coin, raw_sz)

        print(f"🔧 LONG {coin} | mid={mid:.4f} | notional={notional_usdc:.2f} -> sz={sz}")
        # market_open(coin, is_buy, sz, limit_px=None, slippage=...)
        return self.exchange.market_open(coin, True, sz, None, float(self.slippage))

    def close_position(self, coin: str):
        return self.exchange.market_close(coin)

    # -------------------------
    # TP / SL (trigger reduce-only)
    # -------------------------
    def set_tp_sl_for_position(self, coin: str, tp_pct: float = 0.01, sl_pct: float = 0.002):
        """
        Place 2 trigger orders reduce-only:
        - TP (tpsl="tp")
        - SL (tpsl="sl")
        """
        in_pos, pos = self.has_position(coin)
        if not in_pos:
            print("ℹ️ Pas de position, rien à protéger.")
            return None

        szi = float(pos["szi"])
        sz_abs = abs(szi)
        # sécurité: re-round la size à la précision
        sz_abs = self._round_size(coin, sz_abs)

        is_long = szi > 0
        close_is_buy = False if is_long else True  # pour fermer long => sell; fermer short => buy

        entry_px = float(pos.get("entryPx", 0) or 0)
        if entry_px <= 0:
            entry_px = self.get_mid_price(coin)

        if is_long:
            tp_trigger = entry_px * (1 + float(tp_pct))
            sl_trigger = entry_px * (1 - float(sl_pct))
            # fermeture = SELL -> limite un peu plus BAS pour assurer le fill
            tp_limit = tp_trigger * (1 - float(self.slippage))
            sl_limit = sl_trigger * (1 - float(self.slippage))
            tp_trigger = self._round_price(tp_trigger, "down")
            sl_trigger = self._round_price(sl_trigger, "down")
            tp_limit = self._round_price(tp_limit, "down")
            sl_limit = self._round_price(sl_limit, "down")
        else:
            tp_trigger = entry_px * (1 - float(tp_pct))
            sl_trigger = entry_px * (1 + float(sl_pct))
            # fermeture = BUY -> limite un peu plus HAUT pour assurer le fill
            tp_limit = tp_trigger * (1 + float(self.slippage))
            sl_limit = sl_trigger * (1 + float(self.slippage))
            tp_trigger = self._round_price(tp_trigger, "up")
            sl_trigger = self._round_price(sl_trigger, "up")
            tp_limit = self._round_price(tp_limit, "up")
            sl_limit = self._round_price(sl_limit, "up")

        print(
            f"🛡️ TP/SL {coin} | entry={entry_px:.4f} | "
            f"tp={tp_trigger:.4f} (limit {tp_limit:.4f}) | "
            f"sl={sl_trigger:.4f} (limit {sl_limit:.4f}) | sz={sz_abs:.6f}"
        )

        orders = [
            {
                "coin": coin,
                "is_buy": close_is_buy,
                "sz": float(sz_abs),
                "limit_px": float(tp_limit),
                "order_type": {
                    "trigger": {
                        "triggerPx": float(tp_trigger),
                        "isMarket": True,
                        "tpsl": "tp",
                    }
                },
                "reduce_only": True,
            },
            {
                "coin": coin,
                "is_buy": close_is_buy,
                "sz": float(sz_abs),
                "limit_px": float(sl_limit),
                "order_type": {
                    "trigger": {
                        "triggerPx": float(sl_trigger),
                        "isMarket": True,
                        "tpsl": "sl",
                    }
                },
                "reduce_only": True,
            },
        ]

        # grouping "positionTpsl" pour que HL traite ça comme TP/SL de position
        return self.exchange.bulk_orders(orders, None, "positionTpsl")
