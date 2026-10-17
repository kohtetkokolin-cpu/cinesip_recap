package com.cinesip.recap.api

import com.cinesip.recap.ai.Beat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Gemini script generation — plain HTTPS, works from phone unchanged.
 * Free tier, same GOOGLE_API_KEY. Hook-first Burmese recap scripts.
 */
class GeminiClient(private val apiKey: String) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(60, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    private val models = listOf(
        "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"
    )

    /**
     * Generate recap script beats from transcript + evidence summary.
     * Returns beats with Burmese text + English visual query per beat.
     */
    suspend fun writeScript(
        transcript: String,
        style: String,
        targetMinutes: Int,
        evidenceSummary: String
    ): List<Beat> = withContext(Dispatchers.IO) {
        val prompt = buildPrompt(transcript, style, targetMinutes, evidenceSummary)
        var lastErr: Exception? = null
        for (model in models) {
            try {
                return@withContext call(model, prompt)
            } catch (e: Exception) {
                lastErr = e
            }
        }
        throw lastErr ?: Exception("Gemini failed")
    }

    private fun buildPrompt(transcript: String, style: String,
                            mins: Int, evidence: String): String {
        return """
You are a Burmese movie-recap scriptwriter (SoloRecap style).
Write a $mins-minute $style recap script in BURMESE.

RULES (must follow):
13. NO DEAD AIR — every second has narration or sound.
14. OPEN-LOOP ENDING — end with a hook, not a summary.
- Cold open in first 20 seconds (in-medias-res + rewind, or setup + open loop).
- 20-35% commentary (moral/rating/humor), host persona voice.
- Pure Burmese, spoiler-hook energy.

EVIDENCE (scene analysis):
$evidence

TRANSCRIPT (truncated):
${transcript.take(8000)}

Return ONLY a JSON array. Each beat:
{"id":1,"text_my":"Burmese narration...","visual_query_en":"english visual description, e.g. a woman crying in the rain close-up","dur_target":8.5}

Aim for ~${mins * 6} beats. dur_target in seconds, sum ≈ ${mins * 60}.
""".trimIndent()
    }

    private fun call(model: String, prompt: String): List<Beat> {
        val body = JSONObject()
            .put("contents", JSONArray().put(
                JSONObject().put("parts", JSONArray().put(
                    JSONObject().put("text", prompt)))))
            .put("generationConfig", JSONObject()
                .put("temperature", 0.8)
                .put("maxOutputTokens", 8192))
            .toString()
            .toRequestBody("application/json".toMediaType())

        val req = Request.Builder()
            .url("https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent?key=$apiKey")
            .post(body)
            .build()

        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw Exception("Gemini ${resp.code}")
            val text = JSONObject(resp.body!!.string())
                .getJSONArray("candidates").getJSONObject(0)
                .getJSONObject("content").getJSONArray("parts")
                .getJSONObject(0).getString("text")
            return parseBeats(text)
        }
    }

    private fun parseBeats(raw: String): List<Beat> {
        // extract JSON array (may be wrapped in markdown fences)
        val start = raw.indexOf('[')
        val end = raw.lastIndexOf(']')
        if (start < 0 || end < 0) throw Exception("No JSON in Gemini response")
        val arr = JSONArray(raw.substring(start, end + 1))
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            Beat(
                id = o.optInt("id", i + 1),
                textMy = o.getString("text_my"),
                visualQueryEn = o.optString("visual_query_en", ""),
                durTarget = o.optDouble("dur_target", 8.0)
            )
        }
    }
}
