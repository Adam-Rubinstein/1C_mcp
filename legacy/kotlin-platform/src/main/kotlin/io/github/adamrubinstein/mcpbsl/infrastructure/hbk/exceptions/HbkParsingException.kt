package io.github.adamrubinstein.mcpbsl.infrastructure.hbk.exceptions

import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.BlockHandler

/**
 * Базовый класс для исключений, возникающих при разборе HBK файлов.
 *
 * Все исключения, связанные с ошибками парсинга HBK файлов, должны
 * наследоваться от этого класса для обеспечения единообразной обработки ошибок.
 *
 * @param message Сообщение об ошибке
 * @param cause Причина исключения
 */
abstract class HbkParsingException(
    message: String,
    cause: Throwable? = null,
) : Exception(message, cause)

/**
 * Исключение, возникающее при обнаружении неизвестного типа блока страницы.
 *
 * @param blockTitle Заголовок неизвестного блока
 */
class UnknownPageBlockType(
    blockTitle: String,
) : HbkParsingException("Неизвестный тип блока страницы описания `$blockTitle`")

/**
 * Исключение, возникающее при отсутствии реализации обработки для парсера.
 *
 * @param handler Парсер, для которого отсутствует реализация
 */
class HandlerProcessingNotImplemented(
    handler: BlockHandler<*>,
) : HbkParsingException("Не реализована обработка парсера `$handler`")

/**
 * Исключение, возникающее при ошибке парсинга оглавления (Table of Contents).
 *
 * @param message Сообщение об ошибке парсинга
 */
class TocParsingError(
    message: String,
) : HbkParsingException("Ошибка разбора оглавления (Table of content) файла: $message")
