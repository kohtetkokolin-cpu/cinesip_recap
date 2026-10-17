package com.cinesip.recap.api

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.io.File
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/**
 * Edge-TTS via WebSocket — v2 endpoint (Microsoft migrated from v1 in 2026).
 * Free, unlimited. Burmese voice: my-MM-NilarNeural / my-MM-ThihaNeural.
 * 
 * v2 changes:
 * - Endpoint: wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1
 * - Auth: Ocp-Apim-Subscription-Key instead of TrustedClientToken
 * - Requires Sec-MS-GEC header (time-based)
 */
class EdgeTtsClient(private val ctx: Context) {
    private val http = OkHttpClient.Builder()
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    companion object {
        const val VOICE_FEMALE = "my-MM-NilarNeural"
        const val VOICE_MALE = "my-MM-ThihaNeural"
        // v2 endpoint (Microsoft migrated in 2026, v1 now returns 403)
        private const val WSS_URL = "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
        private const val SUBSCRIPTION_KEY = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
    }
    }

    /**
     * Synthesize Burmese text to a wav file. Returns the file path.
     * Applies rate/pitch via SSML prosody.
     */
    suspend fun synthesize(
        text: String,
        outPath: String,
        voice: String = VOICE_FEMALE,
        rate: Double = 1.0,
        pitch: Int = 0,
        volume: Int = 100
    ): String = withContext(Dispatchers.IO) {
        suspendCoroutine { cont ->
            val audioBytes = mutableListOf<Byte>()
            val done = CountDownLatch(1)
            var failed: Exception? = null

            val reqId = UUID.randomUUID().toString().replace("-", "")
            val ssml = """
                <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="my-MM">
                <voice name="$voice"><prosody rate="${((rate - 1) * 100).toInt()}%" pitch="${pitch}Hz" volume="${volume}%">
                ${text.replace("&", "&amp;").replace("<", "&lt;")}
                </prosody></voice></speak>
            """.trimIndent()

            val ws = http.newWebSocket(
                Request.Builder()
                    .url("$WSS_URL?Ocp-Apim-Subscription-Key=$SUBSCRIPTION_KEY")
                    .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")
                    .header("Sec-MS-GEC", generateSecMsGec())
                    .header("Sec-MS-GEC-Version", "1-140.0.3485.14")
                    .build(),
                object : WebSocketListener() {
                    override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                        // audio chunk: header (2 bytes len) + header + data
                        if (bytes.size > 2) {
                            val headerLen = ((bytes[0].toInt() and 0xFF) shl 8) or
                                    (bytes[1].toInt() and 0xFF)
                            val dataStart = 2 + headerLen
                            if (dataStart < bytes.size) {
                                val data = bytes.substring(dataStart)
                                synchronized(audioBytes) {
                                    for (b in data.toByteArray()) audioBytes.add(b)
                                }
                            }
                        }
                    }
                    override fun onMessage(webSocket: WebSocket, text: String) {
                        if (text.contains("turn.end")) {
                            done.countDown()
                            webSocket.close(1000, null)
                        }
                    }
                    override fun onFailure(webSocket: WebSocket, t: Throwable, r: okhttp3.Response?) {
                        failed = Exception(t.message)
                        done.countDown()
                    }
                })

            // config + ssml messages with proper Edge-TTS framing
            val timestamp = java.time.ZonedDateTime.now(java.time.ZoneOffset.UTC)
                .format(java.time.format.DateTimeFormatter.ofPattern("EEE MMM dd yyyy HH:mm:ss 'GMT'XXX", java.util.Locale.US))
            
            // Speech config message
            val configMsg = "X-Timestamp:$timestamp\r\n" +
                    "Content-Type:application/json; charset=utf-8\r\n" +
                    "Path:speech.config\r\n\r\n" +
                    """{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"true"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}"""
            ws.send(configMsg)
            
            // SSML message with proper framing
            val ssmlMsg = "X-RequestId:$reqId\r\n" +
                    "Content-Type:application/ssml+xml\r\n" +
                    "X-Timestamp:$timestamp\r\n" +
                    "Path:ssml\r\n\r\n" +
                    ssml.trim()
            ws.send(ssmlMsg)

            if (!done.await(90, TimeUnit.SECONDS)) {
                failed = Exception("TTS timeout")
            }
            ws.close(1000, null)

            failed?.let { cont.resumeWithException(it); return@suspendCoroutine }
            try {
                File(outPath).writeBytes(audioBytes.toByteArray())
                cont.resume(outPath)
            } catch (e: Exception) {
                cont.resumeWithException(e)
            }
        }
    }

    /**
     * Generate Sec-MS-GEC token (v1 format).
     * SHA-256 of (5-min rounded Windows filetime ticks + trusted client token).
     */
    private fun generateSecMsGec(): String {
        return try {
            // Windows filetime: 100-nanosecond intervals since 1601-01-01
            // Rounded to 5-minute intervals
            val now = System.currentTimeMillis()
            val ticks = (now / 1000 + 11644473600L) * 10000000L
            val roundedTicks = ticks - (ticks % (5 * 60 * 10000000L))
            val input = "$roundedTicks$SUBSCRIPTION_KEY"
            val digest = java.security.MessageDigest.getInstance("SHA-256")
            val hash = digest.digest(input.toByteArray(Charsets.UTF_8))
            hash.joinToString("") { "%02x".format(it) }.uppercase()
        } catch (e: Exception) {
            // Fallback: return empty, server may still accept
            ""
        }
    }
}
