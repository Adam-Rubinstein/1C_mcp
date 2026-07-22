package io.github.adamrubinstein.mcpbsl.infrastructure.hbk

import org.junit.jupiter.api.Test
import io.github.adamrubinstein.mcpbsl.infrastructure.hbk.reader.HbkContentReader
import kotlin.io.path.Path

class HbkReaderTest {
    @Test
    fun read() {
        val platformPath = System.getProperty("platform.context.path")
        val reader = HbkContentReader()
        reader.read(Path(platformPath, "shcntx_ru.hbk")) {
            println("Success")
        }
    }

//    @Test
//    fun readPlatformContextGrabber(@TempDir path: Path){
//        val platformPath = System.getProperty("platform.context.path");
//        val parser = PlatformContextGrabber(Path(platformPath, "shcntx_ru.hbk")), path)
//        parser.parse()
//    }
}
