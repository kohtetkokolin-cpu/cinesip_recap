package com.cinesip.recap.api

import android.content.Context
import com.antonkarpenko.ffmpegkit.FFmpegKit
import com.antonkarpenko.ffmpegkit.ReturnCode
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Gemini TTS via AI Studio REST — primary phone voice.
 *
 * Why this exists: Edge-TTS's WebSocket handshake now returns
 * "403 Forbidden" (Microsoft's 2026 v1→v2 migration; the v2 endpoint
 * requires a keyed <16-hex>-<64-hex> Sec-MS-GEC token whose generation
 * algorithm is not public). Gemini TTS needs only the user's API key
 * (already collected in Settings), supports Burmese, and is free tier.
 *
 * REST: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
 *   header x-goog-api-key: <key>
 *   body: { contents:[{parts:[{text: "Say: ..."}]}], generationConfig:{
 *             responseModalities:["AUDIO"],
 *             speechConfig:{ voiceConfig:{ prebuiltVoiceConfig:{ voiceName } },
 *                            languageCode:"my-MM" } } }
 * Returns base64 audio in candidates[0].content.parts[0].inlineData.data
 * (raw 16-bit PCM 24kHz mono for preview models, WAV for 3.8+).
 */
class GeminiTtsClient(private val ctx: Context, private val apiKey: String) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(60, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .build()

    private val models = listOf(
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
        "gemini-2.5-flash-preview-tts",
        "gemini-2.5-pro-preview-tts"
    )

    /** wizard voice preset id → (Gemini prebuilt voice, style prompt) */
    private val voiceMap = mapOf(
        "thiha"       to ("Charon" to "narrate in a clear, steady voice"),
        "nilar"       to ("Kore"   to "narrate in a warm, friendly voice"),
        "thiha_deep"  to ("Fenrir" to "narrate in a deep, serious, slow voice"),
        "thiha_young" to ("Charon" to "narrate in a young, energetic, upbeat voice"),
        "nilar_deep"  to ("Leda"   to "narrate in a deep, calm, mature voice"),
        "nilar_young" to ("Kore"   to "narrate in a young, cheerful, lively voice"),
        "thiha_fun"   to ("Charon" to "narrate in a playful, humorous, light voice"),
        "nilar_fun"   to ("Aoede"  to "narrate in a playful, cheerful, bright voice"),
        "thiha_calm"  to ("Fenrir" to "narrate in a calm, gentle, reassuring voice"),
        "nilar_calm"  to ("Leda"   to "narrate in a soft, calm, soothing voice")
    )
    // NOTE: Gemini TTS has no SSML rate/pitch. The 10 presets above already
    // encode pitch/speed character as separate voices+styles; playback speed
    // is applied afterwards with ffmpeg atempo (see below).

    /**
     * Synthesize Burmese text → wav file at [outPath]. Returns outPath.
     * @param presetId one of the wizard VOICES ids (thiha, nilar, ...)
     * @param speed    playback rate; applied with atempo when != 1.0
     */
    suspend fun synthesize(
        text: String,
        outPath: String,
        presetId: String = "thiha",
        speed: Double = 1.0
    ): String = withContext(Dispatchers.IO) {
        require(apiKey.isNotBlank()) { "Gemini API key မရှိပါ — Settings မှာ ထည့်ပါ" }
        val (voice, style) = voiceMap[presetId] ?: voiceMap.getValue("thiha")
        // Keep the model focused on speaking; prefix avoids chatty intros.
        val speakText = "Say in Burmese, $style: $text"

        var lastErr: Exception? = null
        for (model in models) {
            try {
                val pcmOrWav = restTts(model, speakText, voice)
                val wavBytes = if (isWav(pcmOrWav)) pcmOrWav else pcmToWav(pcmOrWav)
                File(outPath).writeBytes(wavBytes)
                if (speed != 1.0) applySpeed(outPath, speed)
                return@withContext outPath
            } catch (e: Exception) {
                lastErr = e
            }
        }
        throw lastErr ?: Exception("Gemini TTS failed")
    }

    private fun restTts(model: String, text: String, voice: String): ByteArray {
        val body = JSONObject()
            .put("contents", org.json.JSONArray().put(
                JSONObject().put("parts", org.json.JSONArray().put(
                    JSONObject().put("text", text)))))
            .put("generationConfig", JSONObject()
                .put("responseModalities", org.json.JSONArray().put("AUDIO"))
                .put("speechConfig", JSONObject()
                    .put("voiceConfig", JSONObject()
                        .put("prebuiltVoiceConfig", JSONObject()
                            .put("voiceName", voice)))
                    .put("languageCode", "my-MM")))
            .toString()
            .toRequestBody("application/json".toMediaType())

        val req = Request.Builder()
            .url("https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent")
            .header("x-goog-api-key", apiKey)
            .post(body)
            .build()

        http.newCall(req).execute().use { resp ->
            val raw = resp.body?.string() ?: ""
            if (!resp.isSuccessful) {
                val msg = try {
                    JSONObject(raw).optJSONObject("error")?.optString("message")
                } catch (_: Exception) { null } ?: "HTTP ${resp.code}"
                throw Exception("Gemini TTS $model: $msg")
            }
            val data = JSONObject(raw)
                .getJSONArray("candidates").getJSONObject(0)
                .getJSONObject("content")
                .getJSONArray("parts").getJSONObject(0)
                .getJSONObject("inlineData").getString("data")
            return android.util.Base64.decode(data, android.util.Base64.DEFAULT)
        }
    }

    private fun isWav(b: ByteArray): Boolean =
        b.size > 12 && b[0] == 'R'.code.toByte() && b[1] == 'I'.code.toByte() &&
                b[2] == 'F'.code.toByte() && b[3] == 'F'.code.toByte()

    /** Wrap raw 16-bit PCM 24kHz mono in a WAV header. */
    private fun pcmToWav(pcm: ByteArray): ByteArray {
        val sampleRate = 24000
        val header = java.nio.ByteBuffer.allocate(44).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        header.put("RIFF".toByteArray())
        header.putInt(36 + pcm.size)
        header.put("WAVE".toByteArray())
        header.put("fmt ".toByteArray())
        header.putInt(16)
        header.putShort(1)          // PCM
        header.putShort(1)          // mono
        header.putInt(sampleRate)
        header.putInt(sampleRate * 2)
        header.putShort(2)
        header.putShort(16)
        header.put("data".toByteArray())
        header.putInt(pcm.size)
        return header.array() + pcm
    }

    /** Apply playback speed with atempo (chain for rates > 2x). */
    private fun applySpeed(wavPath: String, speed: Double) {
        var s = speed.coerceIn(0.5, 4.0)
        val filters = mutableListOf<String>()
        while (s > 2.0) { filters.add("atempo=2.0"); s /= 2.0 }
        while (s < 0.5) { filters.add("atempo=0.5"); s *= 2.0 }
        filters.add("atempo=%.2f".format(s))
        val tmp = "$wavPath.sp.wav"
        val r = FFmpegKit.execute("-y -i \"$wavPath\" -af \"${filters.joinToString(",")}\" \"$tmp\"")
        if (ReturnCode.isSuccess(r.returnCode)) {
            File(tmp).copyTo(File(wavPath), overwrite = true)
            File(tmp).delete()
        }
        // If atempo fails, keep the original-speed file rather than failing.
    }
}
