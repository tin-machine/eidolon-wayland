package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ConnectionProfileV2Test {
    private val installation = "1".repeat(32)
    private fun vectors(): List<Pair<Map<String, Any>, String>> = checkNotNull(javaClass.classLoader?.getResourceAsStream("connection-profiles-v2.tsv"))
        .bufferedReader().useLines { lines -> lines.filter { !it.startsWith('#') && it.isNotBlank() }.map { row ->
            val f = row.split('\t')
            mapOf("schema" to 2, "installation" to installation, "profile_id" to f[0], "revision" to f[1],
                "display_name" to f[2], "backend" to f[3], "host" to f[4], "port" to f[5].toInt(),
                "username" to f[6], "host_key" to f[7]) to f[8]
        }.toList() }
    private fun local() = vectors().first().first
    private fun remote() = vectors()[1].first
    private fun parse(value: Map<String, Any?>) = ConnectionProfileV2.parse(value, installation)
    private fun old() = local().filterKeys { it in setOf("schema", "installation", "profile_id", "username", "host_key") } + ("schema" to 1)

    @Test fun sharedRustVectorsPreserveRevisionAndCanonicalBindingWithoutConnecting() {
        for ((fields, expected) in vectors()) {
            val p = parse(fields)
            assertEquals(expected, p.binding())
            assertEquals(fields["revision"], p.toFields()["revision"])
            assertEquals(p.binding(), parse(p.toFields()).binding())
            assertFalse(p.toString().contains(p.hostKey))
            assertEquals(p.backend == ConnectionProfileV2.Backend.TERMUX_LOCAL, p.legacyRuntimeFields() != null)
        }
        assertEquals("host.example", parse(remote()).host)
        assertEquals("linux-build-01", parse(remote() + ("host" to "LINUX-BUILD-01")).host)
    }

    @Test fun legacyMigrationIsExplicitAndCannotAuthorizeOtherEndpointsOrRevisions() {
        val migrated = ConnectionProfileV2.migrateLegacyTermux(old(), installation)
        val p = migrated.profile
        assertEquals(local(), p.toFields())
        assertEquals(old(), p.legacyRuntimeFields())
        val candidate = migrated.legacyCleanupCandidate(p)
        assertNotNull(candidate)
        assertNotEquals(p.binding(), candidate)
        assertNull(migrated.legacyCleanupCandidate(parse(remote())))
        assertNull(migrated.legacyCleanupCandidate(parse(local() + ("revision" to "2"))))
        assertNull(migrated.legacyCleanupCandidate(parse(local() + ("display_name" to "Changed"))))
        for (bad in listOf(old() + ("schema" to 2), old() + ("host" to "192.0.2.1"),
            old() + ("profile_id" to "linux-test"), old() + ("installation" to "2".repeat(32)))) {
            assertThrows(IllegalArgumentException::class.java) { ConnectionProfileV2.migrateLegacyTermux(bad, installation) }
        }
    }

    @Test fun allIdentityDimensionsAffectBindingAndEditsRequireFreshRevisionAndConfirmation() {
        val before = parse(remote())
        for ((field, value) in listOf("host" to "other.example", "port" to 2200, "username" to "other-user",
            "revision" to "9007199254740994", "profile_id" to "other-linux",
            "host_key" to (remote().getValue("host_key") as String).replace("AQEB", "AgIC"))) {
            assertNotEquals(field, before.binding(), parse(remote() + (field to value)).binding())
        }
        val next = remote() + ("revision" to "9007199254740994") + ("host" to "other.example")
        assertEquals("other.example", before.replacement(before.binding(), next, true).host)
        for ((expected, value, confirmed) in listOf(Triple("stale", next, true), Triple(before.binding(), next, false),
            Triple(before.binding(), remote(), true), Triple(before.binding(), next + ("profile_id" to "other-linux"), true),
            Triple(before.binding(), next + ("installation" to "2".repeat(32)), true))) {
            assertThrows(IllegalArgumentException::class.java) { before.replacement(expected, value, confirmed) }
        }
        val max = parse(vectors().last().first)
        assertThrows(IllegalArgumentException::class.java) { max.replacement(max.binding(), max.toFields(), true) }
    }

    @Test fun malformedOrUnsupportedFieldsFailClosedWithoutNumericCoercion() {
        val changes = listOf("revision" to 1, "revision" to "01", "revision" to "0", "revision" to "9223372036854775808",
            "schema" to 2.0, "port" to 22.0, "port" to true, "port" to 0, "port" to 65536,
            "backend" to "unknown", "host" to "user@host.example", "host" to "host.example:22",
            "host" to "127.1", "host" to "127.00.0.1", "host" to "[::1]", "host" to "::1",
            "host" to "host.example.", "host" to "x\n.example", "host" to "a_foo.example",
            "host" to "0x7f000001", "username" to "-user", "username" to "user;id",
            "profile_id" to "termux-local", "display_name" to "\uD800", "display_name" to "line\nbreak")
        for ((field, value) in changes) {
            assertThrows(field, IllegalArgumentException::class.java) { parse(remote() + (field to value)) }
        }
        assertThrows(IllegalArgumentException::class.java) { parse(remote() + ("secret_key" to "fixture")) }
        assertThrows(IllegalArgumentException::class.java) { parse(remote() - "revision") }
        for (changed in listOf(local() + ("host" to "localhost"), local() + ("port" to 22),
            local() + ("backend" to "linux_ssh"), local() + ("username" to "linux-user"))) {
            assertThrows(IllegalArgumentException::class.java) { parse(changed) }
        }
    }

    @Test fun pinCanonicalizationAndDisplayLimitsUseExactWireAndUtf8Bytes() {
        val original = parse(remote())
        assertEquals(original.binding(), parse(remote() + ("host_key" to "${original.hostKey} fixture comment\n")).binding())
        for (key in listOf("ssh-rsa AAAA", "ssh-ed25519 !!!!", "${original.hostKey}\n${original.hostKey}", "x".repeat(4097))) {
            assertThrows(IllegalArgumentException::class.java) { parse(remote() + ("host_key" to key)) }
        }
        assertEquals("あ".repeat(53), parse(remote() + ("display_name" to "あ".repeat(53))).displayName)
        assertThrows(IllegalArgumentException::class.java) { parse(remote() + ("display_name" to "あ".repeat(54))) }
        assertThrows(IllegalArgumentException::class.java) { parse(remote() + ("display_name" to " ")) }
    }
}
