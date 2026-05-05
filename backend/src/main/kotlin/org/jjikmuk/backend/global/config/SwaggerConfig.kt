package org.jjikmuk.backend.global.config

import io.swagger.v3.oas.models.OpenAPI
import io.swagger.v3.oas.models.info.Info
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

@Configuration
class SwaggerConfig {

    @Bean
    fun openAPI(): OpenAPI {
        return OpenAPI()
            .info(
                Info()
                    .title("찍먹 API 명세서")
                    .description("알레르기인을 위한 맞춤형 식품 스캐너 및 AI 대화형 플랫폼 API입니다.")
                    .version("1.0.0")
            )
    }
}