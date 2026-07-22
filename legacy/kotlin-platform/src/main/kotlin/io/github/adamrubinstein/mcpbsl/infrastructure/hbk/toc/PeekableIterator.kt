package io.github.adamrubinstein.mcpbsl.infrastructure.hbk.toc

/**
 * Расширение для Iterator с поддержкой операции peek.
 *
 * Этот класс оборачивает стандартный Iterator и добавляет возможность
 * просмотра следующего элемента без его извлечения из итератора.
 * Это полезно для парсинга, когда нужно "заглянуть" на следующий токен
 * для принятия решения о дальнейшей обработке.
 *
 * Основные возможности:
 * - Стандартные операции Iterator (hasNext, next)
 * - Операция peek для просмотра следующего элемента
 * - Кэширование просмотренного элемента
 *
 * @param T Тип элементов в итераторе
 *
 * @see TocParser для использования в парсинге оглавления
 * @see Tokenizer для получения токенов для итерации
 */
internal class PeekableIterator<T>(
    private val iterator: Iterator<T>,
) : Iterator<T> {
    private var peeked: T? = null

    override fun hasNext(): Boolean = peeked != null || iterator.hasNext()

    override fun next(): T =
        if (peeked != null) {
            val result = peeked!!
            peeked = null
            result
        } else {
            iterator.next()
        }

    /**
     * Просматривает следующий элемент без его извлечения.
     *
     * @return Следующий элемент или null, если элементов больше нет
     */
    fun peek(): T? {
        if (peeked == null && iterator.hasNext()) {
            peeked = iterator.next()
        }
        return peeked
    }
}
