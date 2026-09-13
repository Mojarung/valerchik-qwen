"""Кодировка потоков вывода: логи lite на Windows без кракозябр."""


def use_utf8(*streams: object) -> None:
    """Перевести потоки вывода на UTF-8, если они пишут в другой кодировке.

    На Windows вывод, перенаправленный в файл или трубу, пишется в кодовой
    странице ANSI (cp1251): кириллица в логах lite становилась кракозябрами у
    любого читателя в UTF-8, а символ вне cp1251 («→», «≥») ронял запись лога
    целиком. Настоящей консоли это не вредит — туда Python пишет через
    Unicode-API Windows. Потоки без reconfigure (подмены в тестах) не трогаем.
    """
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = str(getattr(stream, "encoding", "") or "").lower().replace("-", "").replace("_", "")
        if reconfigure is not None and encoding != "utf8":
            reconfigure(encoding="utf-8", errors="backslashreplace")
