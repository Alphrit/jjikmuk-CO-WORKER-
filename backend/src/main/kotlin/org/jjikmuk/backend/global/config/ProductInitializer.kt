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

            // ⚠️ 읽어올 파일명이 'Product.csv'인지, 'final_product_db.csv'인지 맞게 수정해주세요!
            val resource = ClassPathResource("data/Product.csv")
            if (!resource.exists()) {
                println("data/Product.csv 파일이 없어 제품 초기 적재를 건너뜁니다.")
                return@CommandLineRunner
            }
            val batchSize = 1000
            val productList = mutableListOf<Product>()
            val existingBarcodes = mutableSetOf<String>() // 💡 중복 바코드 방어용 Set

            var count = 0

            BufferedReader(InputStreamReader(resource.inputStream, Charsets.UTF_8)).use { reader ->
                var line: String? = reader.readLine()?.replace("\uFEFF", "")
                val headerRegex = ",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)".toRegex()
                val headers = line!!.split(headerRegex).map { it.removeSurrounding("\"").trim() }

                // 🚀 [새롭게 바뀐 17개 영문 헤더 인덱스 매핑]
                val barcodeIdx = headers.indexOf("barcode")
                val productNameIdx = headers.indexOf("product_name")
                val manufacturerIdx = headers.indexOf("manufacturer")
                val reportNoIdx = headers.indexOf("report_no")
                val allergyIdx = headers.indexOf("allergy")
                val nutrientTextIdx = headers.indexOf("nutrient_text")
                val imageIdx = headers.indexOf("image_url")
                val sourceIdx = headers.indexOf("source")
                val rawMaterialsIdx = headers.indexOf("raw_materials")
                val energyKcalIdx = headers.indexOf("energy_kcal")
                val carbsGIdx = headers.indexOf("carbs_g")
                val proteinGIdx = headers.indexOf("protein_g")
                val fatGIdx = headers.indexOf("fat_g")
                val sugarGIdx = headers.indexOf("sugar_g")
                val sodiumMgIdx = headers.indexOf("sodium_mg")
                val cholesterolMgIdx = headers.indexOf("cholesterol_mg")
                val allergyWarningIdx = headers.indexOf("allergy_warning")
                val cleanProductNameIdx = headers.indexOf("clean_product_name")
                val totalWeightIdx = headers.indexOf("total_weight")
                val carbsPercentIdx = headers.indexOf("carbs_percent(%)")
                val proteinPercentIdx = headers.indexOf("protein_percent(%)")
                val fatPercentIdx = headers.indexOf("fat_percent(%)")
                val sodiumGIdx = headers.indexOf("sodium_g")
                val cholesterolGIdx = headers.indexOf("cholesterol_g")

                println("헤더 파싱 완료! 바코드 열의 위치는: $barcodeIdx 번 칸입니다.")

                while (reader.readLine().also { line = it } != null) {
                    try {
                        val data = line!!.split(headerRegex).map { it.removeSurrounding("\"").trim() }

                        if (data.size < headers.size - 2) continue

                        count++

                        fun getString(idx: Int): String? =
                            if (idx in data.indices) data[idx].takeIf { it.isNotBlank() } else null

                        fun getDouble(idx: Int): Double? =
                            getString(idx)?.toDoubleOrNull()

                        // 💡 PK(바코드)가 null일 경우 임시 키 발급
                        val barcodeStr = getString(barcodeIdx) ?: "UNKNOWN_$count"

                        // 💡 바코드 중복(PK 충돌) 시 에러 방지 후 스킵
                        if (existingBarcodes.contains(barcodeStr)) continue
                        existingBarcodes.add(barcodeStr)

                        val product = Product(
                            barcode = barcodeStr,
                            productName = getString(productNameIdx),
                            manufacturer = getString(manufacturerIdx),
                            reportNo = getString(reportNoIdx),
                            allergy = getString(allergyIdx),
                            nutrientText = getString(nutrientTextIdx),
                            imageUrl = getString(imageIdx),
                            source = getString(sourceIdx),
                            rawMaterials = getString(rawMaterialsIdx),
                            energyKcal = getDouble(energyKcalIdx),
                            carbsG = getDouble(carbsGIdx),
                            proteinG = getDouble(proteinGIdx),
                            fatG = getDouble(fatGIdx),
                            sugarG = getDouble(sugarGIdx),
                            sodiumMg = getDouble(sodiumMgIdx),
                            cholesterolMg = getDouble(cholesterolMgIdx),
                            allergyWarning = getString(allergyWarningIdx),
                            cleanProductName = getString(cleanProductNameIdx),
                            totalWeight = getString(totalWeightIdx),
                            carbsPercent = getDouble(carbsPercentIdx),
                            proteinPercent = getDouble(proteinPercentIdx),
                            fatPercent = getDouble(fatPercentIdx),
                            sodiumG = getDouble(sodiumGIdx),
                            cholesterolG = getDouble(cholesterolGIdx)
                        )

                        productList.add(product)

                        if (productList.size >= batchSize) {
                            productRepository.saveAll(productList)
                            productList.clear()

                            val percent = (count / 233799.0) * 100 // 23만건 기준
                            print("\rDB 적재 중: ${String.format("%.1f", percent)}% ($count / 233799)")
                        }

                    } catch (e: Exception) {
                        println("\n데이터 파싱 오류 (행 번호 $count 부근): ${e.message}")
                    }
                }

                // 남은 데이터 마저 저장
                if (productList.isNotEmpty()) {
                    productRepository.saveAll(productList)
                }

                println("\n총 ${existingBarcodes.size} 개의 제품 데이터가 DB에 성공적으로 저장되었습니다!")
            }

            val versionKey = "PRODUCT_DB_VERSION"
            val newVersionName = "V2.0" // DB 스키마가 바뀌었으니 V2로 승격!
            val config = systemConfigRepository.findById(versionKey)
                .orElse(SystemConfig(configKey = versionKey, configValue = newVersionName))

            config.updateVersion(newVersionName)
            systemConfigRepository.save(config)

            println("데이터 마이그레이션 및 버전 기록 완료: $newVersionName (${config.updatedAt})")
        }
    }
}
