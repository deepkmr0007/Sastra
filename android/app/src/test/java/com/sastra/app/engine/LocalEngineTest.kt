package com.sastra.app.engine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalEngineTest {
    private val saved = listOf("Giglana GSS meeting is on Friday", "Prefer answers in Hinglish")
    private val engine = LocalEngine { q -> saved.filter { s -> q.lowercase().split(" ").any { it.length > 3 && s.lowercase().contains(it) } } }

    @Test fun arithmetic() {
        val r = engine.answer("what is 12*(3+4)?")
        assertTrue(r.text.contains("= 84"))
        assertFalse(r.queueForServer)
    }

    @Test fun unitConversion() {
        assertTrue(engine.answer("10 km to mi").text.contains("6.21371"))
    }

    @Test fun rememberCapturesNote() {
        val r = engine.answer("remember: water the mango saplings on Sunday")
        assertEquals("water the mango saplings on Sunday", r.remember)
    }

    @Test fun memoryRecallQueues() {
        val r = engine.answer("when is the giglana meeting")
        assertTrue(r.text.contains("Friday"))
        assertTrue(r.queueForServer)
    }

    @Test fun honestFallback() {
        val r = engine.answer("write me a business plan")
        assertFalse(r.handled)
        assertTrue(r.queueForServer)
        assertTrue(r.text.contains("offline"))
    }
}
