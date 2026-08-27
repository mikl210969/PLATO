"""
Шаг 10.4.2: BinanceRestClient.get_user_trades() (T23).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from adapters.binance_rest import BinanceRestClient


@pytest.fixture
def client():
    return BinanceRestClient(
        api_key="test_key",
        api_secret="test_secret",
        base_url="https://testnet.binancefuture.com"
    )


@pytest.mark.asyncio
async def test_t23_get_user_trades_returns_list(client):
    """get_user_trades должен вернуть список трейдов."""
    mock_trades = [
        {
            "symbol": "SOLUSDT",
            "id": 12345,
            "orderId": 4188269065,
            "side": "SELL",
            "positionSide": "SHORT",
            "price": "101.68",
            "qty": "7.0",
            "quoteQty": "711.76",
            "commission": "0.284",
            "commissionAsset": "USDT",
            "time": 1787746411000,
            "isBuyer": False,
            "isMaker": False,
            "isClosePosition": False,
            "realizedPnl": "0"
        },
        {
            "symbol": "SOLUSDT",
            "id": 12346,
            "orderId": 4188269066,
            "side": "BUY",
            "positionSide": "SHORT",
            "price": "101.37",
            "qty": "3.5",
            "quoteQty": "354.79",
            "commission": "0.141",
            "commissionAsset": "USDT",
            "time": 1787749482000,
            "isBuyer": True,
            "isMaker": False,
            "isClosePosition": True,
            "realizedPnl": "1.08"
        }
    ]
    
    with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_trades
        
        trades = await client.get_user_trades("SOLUSDT", start_time=1787746000000)
        
        assert len(trades) == 2
        assert trades[0]['symbol'] == "SOLUSDT"
        assert trades[1]['isClosePosition'] is True
        
        # Проверяем что запрос был с правильными параметрами
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == 'GET'
        assert call_args[0][1] == '/fapi/v1/userTrades'
        assert call_args[1]['signed'] is True


@pytest.mark.asyncio
async def test_t23b_get_user_trades_handles_error_gracefully(client):
    """get_user_trades должен вернуть пустой список при ошибке, не падая."""
    with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = Exception("Network error")
        
        trades = await client.get_user_trades("SOLUSDT")
        
        assert trades == []


@pytest.mark.asyncio
async def test_t23c_get_user_trades_limits_to_1000(client):
    """get_user_trades должен ограничивать limit максимум 1000."""
    with patch.object(client, '_request', new_callable=AsyncMock) as mock_request:
        mock_request.return_value = []
        
        # Передаём limit больше лимита Binance
        await client.get_user_trades("SOLUSDT", limit=5000)
        
        # Проверяем что limit был ограничен до 1000
        call_args = mock_request.call_args
        assert call_args[0][2]['limit'] == 1000