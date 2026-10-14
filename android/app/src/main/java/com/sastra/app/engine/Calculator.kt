package com.sastra.app.engine

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.ln
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.math.tan

/** Safe recursive-descent arithmetic parser (no reflection, no eval). Works fully offline. */
object Calculator {
    class CalcException(message: String) : Exception(message)

    fun evaluate(input: String): Double {
        val src = input.replace("×", "*").replace("÷", "/").replace(Regex("(?<=\\d),(?=\\d{3}\\b)"), "")
        if (src.length > 300) throw CalcException("Expression too long")
        val p = Parser(src)
        val v = p.parseExpression()
        p.skipWs()
        if (!p.atEnd()) throw CalcException("Unexpected '${p.peek()}'")
        if (v.isNaN() || v.isInfinite()) throw CalcException("Result is not a finite number")
        return v
    }

    fun format(v: Double): String =
        if (v == Math.floor(v) && abs(v) < 1e15) v.toLong().toString() else "%.10g".format(v).trimEnd('0').trimEnd('.')

    fun looksLikeMath(text: String): Boolean {
        val t = text.trim().removeSuffix("?").removeSuffix("=").trim()
        return t.isNotEmpty() && t.any { it.isDigit() } &&
            Regex("^[\\d\\s.+\\-*/()%^,×÷a-z]+$").matches(t.lowercase()) &&
            t.any { it in "+-*/^%×÷(" }
    }

    private class Parser(val s: String) {
        var i = 0
        fun atEnd() = i >= s.length
        fun peek() = s[i]
        fun skipWs() { while (!atEnd() && s[i].isWhitespace()) i++ }
        fun eat(c: Char): Boolean { skipWs(); if (!atEnd() && s[i] == c) { i++; return true }; return false }

        fun parseExpression(): Double {
            var v = parseTerm()
            while (true) {
                v = when {
                    eat('+') -> v + parseTerm()
                    eat('-') -> v - parseTerm()
                    else -> return v
                }
            }
        }

        fun parseTerm(): Double {
            var v = parseFactor()
            while (true) {
                v = when {
                    eat('*') -> v * parseFactor()
                    eat('/') -> { val d = parseFactor(); if (d == 0.0) throw CalcException("Division by zero"); v / d }
                    eat('%') -> v % parseFactor()
                    else -> return v
                }
            }
        }

        fun parseFactor(): Double {
            val base = parseUnary()
            if (eat('^')) {
                val e = parseFactor()
                if (abs(e) > 1000) throw CalcException("Exponent too large")
                return base.pow(e)
            }
            return base
        }

        fun parseUnary(): Double = when {
            eat('-') -> -parseUnary()
            eat('+') -> parseUnary()
            else -> parsePrimary()
        }

        fun parsePrimary(): Double {
            skipWs()
            if (eat('(')) {
                val v = parseExpression()
                if (!eat(')')) throw CalcException("Missing )")
                return v
            }
            if (!atEnd() && s[i].isLetter()) {
                val start = i
                while (!atEnd() && s[i].isLetterOrDigit()) i++
                val name = s.substring(start, i).lowercase()
                when (name) {
                    "pi" -> return Math.PI
                    "e" -> return Math.E
                }
                if (!eat('(')) throw CalcException("Unknown name '$name'")
                val arg = parseExpression()
                if (!eat(')')) throw CalcException("Missing )")
                return when (name) {
                    "sqrt" -> sqrt(arg)
                    "ln", "log" -> ln(arg)
                    "log10" -> log10(arg)
                    "sin" -> sin(arg)
                    "cos" -> cos(arg)
                    "tan" -> tan(arg)
                    "abs" -> abs(arg)
                    else -> throw CalcException("Unknown function '$name'")
                }
            }
            val start = i
            while (!atEnd() && (s[i].isDigit() || s[i] == '.')) i++
            if (start == i) throw CalcException(if (atEnd()) "Unexpected end" else "Unexpected '${s[i]}'")
            return s.substring(start, i).toDoubleOrNull() ?: throw CalcException("Bad number")
        }
    }
}
