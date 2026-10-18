package com.cinesip.recap

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.*
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private var pickedPath: String? = null

    private val pickVideo = registerForActivityResult(
        ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let {
            pickedPath = FileUtil.getPath(this, it) ?: it.toString()
            web.evaluateJavascript("onVideoPicked('$pickedPath')", null)
        }
    }

    private val pickAudio = registerForActivityResult(
        ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let {
            val path = FileUtil.getPath(this, it) ?: it.toString()
            web.evaluateJavascript("onAudioPicked('$path')", null)
        }
    }

    private val pickLogo = registerForActivityResult(
        ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let {
            val path = FileUtil.getPath(this, it) ?: it.toString()
            web.evaluateJavascript("onLogoPicked('$path')", null)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Splash with motion (like Zippy)
        setContentView(R.layout.activity_splash)
        val logo = findViewById<android.widget.ImageView>(R.id.splashLogo)
        val title = findViewById<android.widget.TextView>(R.id.splashTitle)
        // Pop-in animation: scale + fade
        logo.scaleX = 0.5f; logo.scaleY = 0.5f; logo.alpha = 0f
        title.alpha = 0f; title.translationY = 30f
        logo.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(600)
            .setInterpolator(android.view.animation.OvershootInterpolator()).start()
        title.animate().alpha(1f).translationY(0f).setDuration(500).setStartDelay(300).start()
        // Load WebView after splash
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            initWebView()
            // Fade in webview
            web.alpha = 0f
            web.animate().alpha(1f).setDuration(400).start()
        }, 1400)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun initWebView() {
        setContentView(R.layout.activity_main)
        web = findViewById(R.id.mainWeb)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = true
        }
        web.webChromeClient = WebChromeClient()
        web.addJavascriptInterface(CinesipBridge(this), "CinesipBridge")
        web.loadUrl("file:///android_asset/wizard.html")
    }

    fun openPicker() = pickVideo.launch("video/*")
    fun openAudioPicker() = pickAudio.launch("audio/*")
    fun openLogoPicker() = pickLogo.launch("image/*")
    fun getPicked(): String? = pickedPath
    fun webEval(js: String) { web.evaluateJavascript(js, null) }
    fun scope() = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.Main)
    fun playAudio(path: String) {
        try {
            val mp = android.media.MediaPlayer()
            mp.setDataSource(path)
            mp.prepare()
            mp.start()
            mp.setOnCompletionListener { it.release() }
        } catch (e: Exception) { toast("❌ " + (e.message ?: "error")) }
    }
    fun prefs(key: String): String? =
        getSharedPreferences("cinesip", MODE_PRIVATE).getString(key, null)
    fun prefs(key: String, value: String) =
        getSharedPreferences("cinesip", MODE_PRIVATE).edit().putString(key, value).apply()
    fun toast(m: String) =
        runOnUiThread { Toast.makeText(this, m, Toast.LENGTH_SHORT).show() }
    fun onProgress(p: Int, s: String) =
        runOnUiThread { web.evaluateJavascript("onProgress($p,'$s')", null) }
    fun onRenderDone(path: String) =
        runOnUiThread { web.evaluateJavascript("onRenderDone('$path')", null) }

    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }
}
