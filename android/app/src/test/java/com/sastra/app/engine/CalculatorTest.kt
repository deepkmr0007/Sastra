package com.sastra.app.engine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CalculatorTest {
    private fun calc(s: String) = Calculator.format(Calculator.evaluate(s))

    @Test fun precedenceAndParentheses() {
        assertEquals("14", calc("2+3*4"))
        assertEquals("20", calc("(2+3)*4"))
        assertEquals("1024", calc("2^10"))
        assertEquals("-6", calc("-2*3"))
        assertEquals("2.5", calc("10/4"))
    }

    @Test fun functionsAndSeparators() {
        assertEquals("4", calc("sqrt(16)"))
        assertEquals("2000", calc("1,000*2"))
        assertEquals("3.141592654", calc("pi"))
    }

    @Test(expected = Calculator.CalcException::class)
    fun divisionByZero() { Calculator.evaluate("1/0") }

    @Test(expected = Calculator.CalcException::class)
    fun rejectsGarbage() { Calculator.evaluate("2 + system.exit()") }

    @Test fun looksLikeMath() {
        assertTrue(Calculator.looksLikeMath("12*(3+4)"))
        assertFalse(Calculator.looksLikeMath("hello there"))
        assertFalse(Calculator.looksLikeMath("2026"))
    }
}
