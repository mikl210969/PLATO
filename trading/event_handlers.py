from trading.handlers.signal_handler import SignalHandlerMixin
from trading.handlers.order_handler import OrderHandlerMixin
from trading.handlers.account_handler import AccountHandlerMixin

class EventHandlersMixin(SignalHandlerMixin, OrderHandlerMixin, AccountHandlerMixin):
    """
    Главный миксин обработчиков событий.
    Наследует логику из трех специализированных модулей, сохраняя доступ ко всем self.* атрибутам.
    """
    pass