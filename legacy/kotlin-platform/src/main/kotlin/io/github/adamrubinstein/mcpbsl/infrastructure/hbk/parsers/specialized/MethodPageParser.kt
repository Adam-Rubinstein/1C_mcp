package io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized

import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.exceptions.HandlerProcessingNotImplemented
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.exceptions.UnknownPageBlockType
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.MethodInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.MethodSignatureInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.RelatedObject
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.ValueInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.BlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.DescriptionBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.ExampleBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.NameBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.NoteBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.PageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.PageProxyHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.ParametersBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.RelatedObjectsBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.SignatureDescriptionBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.SyntaxBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.ValueInfoBlockHandler

/**
 * Обработчик для парсинга страниц методов объектов платформы 1С:Предприятие.
 *
 * Этот класс отвечает за извлечение и структурирование информации о методах
 * из HTML страниц документации HBK. Он поддерживает обработку множественных
 * сигнатур (перегрузок) метода и извлекает информацию о параметрах,
 * возвращаемых значениях, описании и примерах использования.
 *
 * Поддерживаемые блоки:
 * - Синтаксис: синтаксис вызова метода
 * - Параметры: список параметров метода
 * - Возвращаемое значение: информация о типе и описании возвращаемого значения
 * - Описание: подробное описание функциональности
 * - Пример: примеры использования
 * - См. также: связанные объекты
 * - Примечание: дополнительные заметки
 *
 * Особенности:
 * - Поддержка множественных сигнатур (перегрузок) метода
 * - Обработка вариантов синтаксиса для разных перегрузок
 * - Извлечение названий на русском и английском языках
 *
 * @see PageProxyHandler для базовой функциональности
 * @see MethodInfo для структуры результата
 * @see MethodSignatureInfo для информации о сигнатурах
 */
class MethodPageProxyHandler : PageProxyHandler<MethodInfo>() {
    private var nameRu: String? = null
    private var nameEn: String? = null
    private val signatures = mutableListOf<MethodSignatureInfo>()
    private var example: String? = null
    private var relatedObjects: List<RelatedObject>? = null
    private var note: String? = null
    private var returnValue: ValueInfo? = null
    private var description: String = ""

    private val currentSignature: MethodSignatureInfo
        get() = signatures.last()

    override fun createHandler(blockTitle: String): BlockHandler<*>? =
        if (blockTitle.startsWith("Вариант синтаксиса:")) {
            null
        } else {
            when (blockTitle) {
                "Синтаксис:", "Вариант синтаксиса:" -> SyntaxBlockHandler()
                "Параметры:" -> ParametersBlockHandler()
                "Возвращаемое значение:" -> ValueInfoBlockHandler()
                "Описание:" -> DescriptionBlockHandler()
                "Описание варианта метода:" -> SignatureDescriptionBlockHandler()
                "Пример:" -> ExampleBlockHandler() // Placeholder, can be a specific handler
                "См. также:" -> RelatedObjectsBlockHandler() // Placeholder, can be a specific handler
                "Примечание:" -> NoteBlockHandler()
                "Доступность:", "Использование в версии:", "Использование в интерфейсе:" -> null
                else -> throw UnknownPageBlockType(blockTitle)
            }
        }

    override fun onBlockStarted(
        text: String,
        handler: BlockHandler<*>?,
    ) {
        if (text.startsWith("Вариант синтаксиса:")) {
            appendNewSignature(text.substring(19).trim())
        }
    }

    override fun onBlockFinished(handler: BlockHandler<*>) {
        when (handler) {
            is NameBlockHandler ->
                handler.getResult().apply {
                    nameRu = first
                    nameEn = second
                }

            is SyntaxBlockHandler -> {
                if (signatures.isEmpty()) {
                    appendNewSignature("Основная")
                }
                currentSignature.syntax = handler.getResult()
            }

            is ParametersBlockHandler -> currentSignature.parameters = handler.getResult()
            is ValueInfoBlockHandler -> returnValue = handler.getResult()
            is SignatureDescriptionBlockHandler -> currentSignature.description = handler.getResult()
            is DescriptionBlockHandler -> description = handler.getResult()
            is ExampleBlockHandler -> example = handler.getResult()
            is RelatedObjectsBlockHandler -> relatedObjects = handler.getResult()
            is NoteBlockHandler -> note = handler.getResult()
            else -> throw HandlerProcessingNotImplemented(handler)
        }
    }

    override fun getResult(): MethodInfo =
        MethodInfo(
            nameRu = nameRu as String,
            nameEn = nameEn as String,
            signatures = signatures,
            example = example,
            relatedObjects = relatedObjects,
            note = note,
            description = description,
            returnValue = returnValue,
        )

    override fun clean() {
        nameRu = null
        nameEn = null
        signatures.clear()
        example = null
        relatedObjects = null
        note = null
        description = ""
        returnValue = null
    }

    fun appendNewSignature(blockTitle: String) {
        signatures += MethodSignatureInfo(blockTitle, "", mutableListOf(), "")
    }
}

/**
 * Парсер для страниц методов объектов платформы 1С:Предприятие.
 *
 * Этот класс специализируется на парсинге HTML страниц документации,
 * содержащих информацию о методах объектов. Он извлекает структурированную
 * информацию о синтаксисе, параметрах, возвращаемых значениях, описании
 * и примерах использования методов, включая поддержку множественных сигнатур.
 *
 * Основные возможности:
 * - Парсинг множественных сигнатур (перегрузок) метода
 * - Извлечение списка параметров с типами и описаниями
 * - Обработка информации о возвращаемых значениях
 * - Извлечение описания функциональности
 * - Обработка примеров использования
 * - Поддержка связанных объектов и заметок
 * - Извлечение названий на двух языках
 *
 * @see PageParser для базовой функциональности парсинга
 * @see MethodInfo для структуры результата
 * @see MethodPageProxyHandler для обработки конкретных блоков
 */
class MethodPageParser : PageParser<MethodInfo>(MethodPageProxyHandler())
