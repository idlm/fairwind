package club.noclub.accelerator

import android.app.Activity
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import club.noclub.accelerator.ui.navigation.AcceleratorApp
import club.noclub.accelerator.ui.theme.AcceleratorTheme

/**
 * The single activity (spec 88, 132).
 *
 * It owns exactly one platform concern the Compose tree cannot: the **system VPN consent
 * dialog**. `VpnService.prepare` must be launched from an Activity with a result
 * contract, and the user's answer must be honoured — approved, or declined and reported
 * without a silent retry. Nothing else lives here; every screen is a composable.
 *
 * The consent result is passed down as a callback so the 首页 screen can show
 * "用户未授权" honestly instead of appearing to work.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val controller = (application as AcceleratorApplication).controller

        setContent {
            // The last consent outcome, so the UI can state what the user did.
            var consentDeclined by remember { mutableStateOf(false) }

            val launcher = rememberLauncherForActivityResult(
                contract = ActivityResultContracts.StartActivityForResult(),
            ) { result ->
                // RESULT_OK means the user approved. Anything else is a refusal and must
                // be shown as one - never silently retried (spec 88).
                consentDeclined = result.resultCode != Activity.RESULT_OK
            }

            AcceleratorTheme {
                AcceleratorApp(
                    controller = controller,
                    consentDeclined = consentDeclined,
                    onRequestVpnConsent = {
                        // A non-null intent means consent is still missing.
                        controller.vpnConsentIntent()?.let { launcher.launch(it) }
                    },
                )
            }
        }
    }
}
