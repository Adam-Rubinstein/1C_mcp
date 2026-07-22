package io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized

import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.exceptions.HandlerProcessingNotImplemented
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.exceptions.UnknownPageBlockType
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.ConstructorInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.MethodParameterInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.RelatedObject
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.BlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.DescriptionBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.ExampleBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.NameBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.NoteBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.PageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.PageProxyHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.ParametersBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.RelatedObjectsBlockHandler
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.SyntaxBlockHandler

/**
 * Обработчик для парсинга страниц конструкторов объектов платформы 1С:Предприятие.
 *
 * Этот класс отвечает за извлечение и структурирование информации о конструкторах
 * из HTML страниц документации HBK. Он обрабатывает различные блоки страницы,
 * такие как синтаксис, параметры, описание, примеры и связанные объекты.
 *
 * Поддерживаемые блоки:
 * - Синтаксис: синтаксис вызова конструктора
 * - Параметры: список параметров конструктора
 * - Описание: подробное описание функциональности
 * - Пример: примеры использования
 * - См. также: связанные объекты
 * - Примечание: дополнительные заметки
 *
 * @see PageProxyHandler для базовой функциональности
 * @see ConstructorInfo для структуры результата
 */
class ConstructorPageProxyHandler : PageProxyHandler<ConstructorInfo>() {
    private var name = ""
    private var syntax = ""
    private var parameters = listOf<MethodParameterInfo>()
    private var description = ""
    private var example: String? = null
    private var relatedObjects: List<RelatedObject>? = null
    private var note: String? = null

    override fun createHandler(blockTitle: String): BlockHandler<*>? =
        when (blockTitle) {
            "Синтаксис:" -> SyntaxBlockHandler()
            "Параметры:" -> ParametersBlockHandler()
            "Описание:" -> DescriptionBlockHandler()
            "Пример:" -> ExampleBlockHandler()
            "См. также:" -> RelatedObjectsBlockHandler()
            "Примечание:" -> NoteBlockHandler()
            "Доступность:", "Использование в версии:", "Использование в интерфейсе:" -> null
            else -> throw UnknownPageBlockType(blockTitle)
        }

    override fun onBlockFinished(handler: BlockHandler<*>) {
        when (handler) {
            is NameBlockHandler ->
                name = handler.getResult().first

            is SyntaxBlockHandler -> syntax = handler.getResult()
            is ParametersBlockHandler -> parameters = handler.getResult()
            is DescriptionBlockHandler -> description = handler.getResult()
            is ExampleBlockHandler -> example = handler.getResult()
            is RelatedObjectsBlockHandler -> relatedObjects = handler.getResult()
            is NoteBlockHandler -> note = handler.getResult()
            else -> throw HandlerProcessingNotImplemented(handler)
        }
    }

    override fun getResult(): ConstructorInfo =
        ConstructorInfo(
            name = name,
            syntax = syntax,
            parameters = parameters,
            description = description.trim(),
            example = example,
            note = note,
            relatedObjects = relatedObjects,
        )

    override fun clean() {
        name = ""
        syntax = ""
        parameters = listOf()
        description = ""
        example = null
        relatedObjects = null
        note = null
    }
}

/**
 * Парсер для страниц конструкторов объектов платформы 1С:Предприятие.
 *
 * Этот класс специализируется на парсинге HTML страниц документации,
 * содержащих информацию о конструкторах объектов. Он извлекает структурированную
 * информацию о синтаксисе, параметрах, описании и примерах использования конструкторов.
 *
 * Основные возможности:
 * - Парсинг синтаксиса конструктора
 * - Извлечение списка параметров с типами и описаниями
 * - Обработка описания функциональности
 * - Извлечение примеров использования
 * - Обработка связанных объектов и заметок
 *
 * @see PageParser для базовой функциональности парсинга
 * @see ConstructorInfo для структуры результата
 * @see ConstructorPageProxyHandler для обработки конкретных блоков
 */
class ConstructorPageParser : PageParser<ConstructorInfo>(ConstructorPageProxyHandler())
