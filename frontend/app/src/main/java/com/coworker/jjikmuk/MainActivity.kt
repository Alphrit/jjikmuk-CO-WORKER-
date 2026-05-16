package com.coworker.jjikmuk

import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import androidx.appcompat.app.AppCompatActivity
import com.coworker.jjikmuk.feature.home.HomeFragment

class MainActivity : AppCompatActivity() {

    /**
     * 시작 화면 변경 방법
     *
     * 1) 시작 Fragment를 바꾸고 싶으면 아래 startFragment의 HomeFragment()를
     *    원하는 Fragment()로 바꾸면 됩니다.
     *
     *    예시 1: 로그인 화면으로 시작하고 싶을 때
     *    import com.coworker.jjikmuk.feature.auth.login.LoginFragment
     *    val startFragment = LoginFragment()
     *
     *    예시 2: 알레르기 선택 화면으로 시작하고 싶을 때
     *    import com.coworker.jjikmuk.feature.auth.allergy.AllergySelectFragment
     *    val startFragment = AllergySelectFragment()
     *
     * 2) 시작 XML 레이아웃을 바꾸고 싶으면 setContentView(R.layout.activity_main)의
     *    activity_main을 원하는 XML 파일명으로 바꾸면 됩니다.
     *
     *    예시:
     *    setContentView(R.layout.activity_splash)
     *
     * 3) 단, 아래 replace(R.id.mainContainer, startFragment)를 계속 사용할 경우
     *    해당 XML 안에는 android:id="@+id/mainContainer"를 가진 컨테이너가 있어야 합니다.
     */
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Activity가 처음 사용할 XML 레이아웃입니다.
        // 다른 XML을 시작 레이아웃으로 쓰고 싶으면 activity_main을 해당 파일명으로 바꾸면 됩니다.
        setContentView(R.layout.activity_main)

        showSystemBarsConsistently()

        if (savedInstanceState == null) {
            // 앱이 처음 실행될 때 mainContainer 안에 보여줄 시작 Fragment입니다.
            // 현재는 홈 화면으로 시작합니다.
            val startFragment = HomeFragment()

            // 로그인 화면으로 시작하고 싶으면 위의 HomeFragment() 대신 LoginFragment()를 사용하면 됩니다.
            // 사용 예시:
            // 1) 파일 상단 import에 아래 줄 추가
            //    import com.coworker.jjikmuk.feature.auth.login.LoginFragment
            //
            // 2) 위 startFragment 코드를 아래처럼 변경
            //    val startFragment = LoginFragment()

            // R.id.mainContainer는 activity_main.xml 안에 있어야 하는 Fragment 컨테이너 id입니다.
            supportFragmentManager.beginTransaction()
                .replace(R.id.mainContainer, startFragment)
                .commit()
        }
    }

    private fun showSystemBarsConsistently() {
        window.clearFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)

        window.statusBarColor = Color.WHITE
        window.navigationBarColor = Color.WHITE

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(true)

            window.decorView.post {
                window.insetsController?.show(
                    WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars()
                )

                window.insetsController?.setSystemBarsAppearance(
                    WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS,
                    WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                )
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        }
    }
}