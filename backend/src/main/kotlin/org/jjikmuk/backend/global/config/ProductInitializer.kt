package org.jjikmuk.backend.global.config

import org.jjikmuk.backend.domain.config.SystemConfig
import org.jjikmuk.backend.domain.config.SystemConfigRepository
import org.jjikmuk.backend.domain.product.Product
import org.jjikmuk.backend.domain.product.ProductRepository
import org.springframework.boot.CommandLineRunner
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.io.ClassPathResource
import java.io.BufferedReader
import java.io.InputStreamReader
import java.nio.charset.Charset

@Configuration
class ProductInitializer(
    private val productRepository: ProductRepository,
    private val systemConfigRepository: SystemConfigRepository
) {

    @Bean
    fun run(): CommandLineRunner {
        return CommandLineRunner {
            if (productRepository.count() > 0) {
                println("이미 제품 데이터가 존재합니다. 초기화 로직을 건너뜁니다.")
                return@CommandLineRunner
            }

            println("Product.csv 데이터를 DB로 마이그레이션 시작...")

            val resource = ClassPathResource("data/Product.csv")
            val batchSize = 1000
            val productList = mutableListOf<Product>()

            var count = 0

            BufferedReader(InputStreamReader(resource.inputStream, Charsets.UTF_8)).use { reader ->
                var line: String? = reader.readLine()?.replace("\uFEFF", "")
                val headerRegex = ",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)".toRegex()
                val headers = line!!.split(headerRegex).map { it.removeSurrounding("\"").trim() }

                // [기본 정보 인덱스]
                val reportNoIdx = headers.indexOf("품목제조보고번호")
                val barcodeIdx = headers.indexOf("barcode")
                val foodCodeIdx = headers.indexOf("식품코드")
                val productNameIdx = headers.indexOf("식품명")
                val manufacturerIdx = headers.indexOf("제조사명")
                val prdlstDcnmIdx = headers.indexOf("PRDLST_DCNM")
                val imageIdx = headers.indexOf("image_url")
                val allergyIdx = headers.indexOf("allergy")
                val rawmtrlNmIdx = headers.indexOf("RAWMTRL_NM")

                // [영양 성분 인덱스]
                val servingSizeIdx = headers.indexOf("영양성분함량기준량")
                val caloriesIdx = headers.indexOf("에너지(kcal)")
                val carbsIdx = headers.indexOf("탄수화물(g)")
                val proteinIdx = headers.indexOf("단백질(g)")
                val fatIdx = headers.indexOf("지방(g)")
                val sugarIdx = headers.indexOf("당류(g)")
                val sodiumIdx = headers.indexOf("나트륨(mg)")
                val cholesterolIdx = headers.indexOf("콜레스테롤(mg)")
                val saturatedFatIdx = headers.indexOf("포화지방산(g)")
                val transFatIdx = headers.indexOf("트랜스지방산(g)")

                println("헤더 파싱 완료! 바코드 열의 위치는: $barcodeIdx 번 칸입니다.")

                var count = 0
                while (reader.readLine().also { line = it } != null) {
                    try {
                        val regex = ",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)".toRegex()
                        val data = line!!.split(regex).map { it.removeSurrounding("\"").trim() }

                        if (data.size < headers.size - 2) continue

                        count++

                        fun getString(idx: Int): String? =
                            if (idx != -1) data.getOrNull(idx)?.takeIf { it.isNotBlank() } else null

                        fun getDouble(idx: Int): Double? =
                            getString(idx)?.toDoubleOrNull()

                        val product = Product(
                            reportNo = getString(reportNoIdx) ?: "UNKNOWN_${count}",

                            barcode = getString(barcodeIdx),
                            foodCode = getString(foodCodeIdx),
                            productName = getString(productNameIdx),
                            manufacturer = getString(manufacturerIdx),
                            prdlstDcnm = getString(prdlstDcnmIdx),
                            imageUrl = getString(imageIdx),
                            allergy = getString(allergyIdx),
                            rawmtrlNm = getString(rawmtrlNmIdx),

                            servingSize = getString(servingSizeIdx),
                            calories = getDouble(caloriesIdx),
                            carbs = getDouble(carbsIdx),
                            protein = getDouble(proteinIdx),
                            fat = getDouble(fatIdx),
                            sugar = getDouble(sugarIdx),
                            sodium = getDouble(sodiumIdx),
                            cholesterol = getDouble(cholesterolIdx),
                            saturatedFat = getDouble(saturatedFatIdx),
                            transFat = getDouble(transFatIdx)
                        )

                        productList.add(product)

                        if (productList.size >= batchSize) {
                            productRepository.saveAll(productList)
                            productList.clear()

                            val percent = (count / 240000.0) * 100
                            print("\rDB 적재 중: ${String.format("%.1f", percent)}% ($count / 240000)")
                        }

                    } catch (e: Exception) {
                        println("\n데이터 파싱 오류 (행 번호 $count 부근): ${e.message}")
                    }
                }

                // 남은 데이터 마저 저장
                if (productList.isNotEmpty()) {
                    productRepository.saveAll(productList)
                }

                println("\n총 $count 개의 제품 데이터가 DB에 성공적으로 저장되었습니다!")
            }
            val versionKey = "PRODUCT_DB_VERSION"
            val newVersionName = "V1.0"
            val config = systemConfigRepository.findById(versionKey)
                .orElse(SystemConfig(configKey = versionKey, configValue = newVersionName))

            config.updateVersion(newVersionName)
            systemConfigRepository.save(config)

            println("데이터 마이그레이션 및 버전 기록 완료: $newVersionName (${config.updatedAt})")
        }
    }
}