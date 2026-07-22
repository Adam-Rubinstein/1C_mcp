package io.github.adamrubinstein.mcpbsl.install

import com.fasterxml.jackson.databind.ObjectMapper
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

/**
 * Контракт JSON для [.cursor/mcp.json], который пишут install-mcp.ps1 / install-mcp.sh.
 */
class McpInstallJsonContractTest {
    private val mapper = ObjectMapper()

    @Test
    fun `mcp json shape matches install scripts output`() {
        val tree =
            mapper.readTree(
                """
                {
                  "mcpServers": {
                    "1c-platform": {
                      "type": "stdio",
                      "command": "/usr/bin/java",
                      "args": [
                        "-Dfile.encoding=UTF-8",
                        "-jar",
                        "/tmp/1C_mcp_bsl.jar",
                        "--platform-path",
                        "/opt/1cv8/x86_64/8.3.27.1508"
                      ]
                    }
                  }
                }
                """.trimIndent(),
            )
        assertThat(tree["mcpServers"]["1c-platform"]["type"].asText()).isEqualTo("stdio")
        val args = tree["mcpServers"]["1c-platform"]["args"]
        assertThat(args.map { it.asText() }).containsExactly(
            "-Dfile.encoding=UTF-8",
            "-jar",
            "/tmp/1C_mcp_bsl.jar",
            "--platform-path",
            "/opt/1cv8/x86_64/8.3.27.1508",
        )
    }
}
