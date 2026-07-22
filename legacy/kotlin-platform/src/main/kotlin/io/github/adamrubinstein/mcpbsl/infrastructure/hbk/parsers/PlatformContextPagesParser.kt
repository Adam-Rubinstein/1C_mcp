package io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers

import io.github.adamrubinstein.mcpbsl.exceptions.PlatformContextLoadException
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.EnumInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.ObjectInfo
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.models.Page
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.core.PageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.ConstructorPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.EnumPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.EnumValuePageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.MethodPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.ObjectPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.parsers.specialized.PropertyPageParser
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.reader.HbkContentReader

/**
 * Координатор парсеров для различных типов страниц HBK документации.
 *
 * Этот класс управляет коллекцией специализированных парсеров для
 * различных типов страниц документации платформы 1С:Предприятие.
 * Он предоставляет единый интерфейс для парсинга страниц и
 * делегирует работу соответствующим парсерам.
 *
 * Основные возможности:
 * - Парсинг страниц свойств объектов
 * - Парсинг страниц методов
 * - Парсинг страниц перечислений и их значений
 * - Парсинг страниц объектов
 * - Парсинг страниц конструкторов
 *
 * @see PropertyPageParser для парсинга страниц свойств
 * @see MethodPageParser для парсинга страниц методов
 * @see EnumPageParser для парсинга страниц перечислений
 * @see ObjectPageParser для парсинга страниц объектов
 * @see ConstructorPageParser для парсинга страниц конструкторов
 */
class PlatformContextPagesParser(
    private val context: HbkContentReader.Context,
) {
    val propertyPageParser = PropertyPageParser()
    val methodPageParser = MethodPageParser()
    val enumPageParser = EnumPageParser()
    val enumValuePageParser = EnumValuePageParser()
    val objectPageParser = ObjectPageParser()
    val constructorPageParser = ConstructorPageParser()

    /**
     * Парсит страницу свойства.
     *
     * @param page Страница свойства
     * @return Информация о свойстве
     */
    fun parsePropertyPage(page: Page) = parsePage(page, propertyPageParser)

    /**
     * Парсит страницу метода.
     *
     * @param page Страница метода
     * @return Информация о методе
     */
    fun parseMethodPage(page: Page) = parsePage(page, methodPageParser)

    /**
     * Парсит страницу перечисления.
     *
     * @param page Страница перечисления
     * @return Информация о перечислении
     */
    fun parseEnumPage(page: Page): EnumInfo = parsePage(page, enumPageParser)

    /**
     * Парсит страницу значения перечисления.
     *
     * @param page Страница значения перечисления
     * @return Информация о значении перечисления
     */
    fun parseEnumValuePage(page: Page) = parsePage(page, enumValuePageParser)

    /**
     * Парсит страницу объекта.
     *
     * @param page Страница объекта
     * @return Информация об объекте
     */
    fun parseObjectPage(page: Page): ObjectInfo = parsePage(page, objectPageParser)

    /**
     * Парсит страницу конструктора.
     *
     * @param page Страница конструктора
     * @return Информация о конструкторе
     */
    fun parseConstructorPage(page: Page) = parsePage(page, constructorPageParser)

    /**
     * Универсальный метод для парсинга страниц с использованием соответствующего парсера.
     *
     * @param page Страница для парсинга
     * @param parser Парсер для обработки страницы
     * @return Результат парсинга
     * @throws PlatformContextLoadException если не удалось разобрать страницу
     */
    private fun <T> parsePage(
        page: Page,
        parser: PageParser<T>,
    ): T {
        try {
            return context.getEntryStream(page).use { parser.parse(it) }
        } catch (ex: Exception) {
            throw PlatformContextLoadException("Не удалось разобрать страницу документации ${page.title}(${page.htmlPath})", ex)
        }
    }
}
