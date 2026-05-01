package org.jjikmuk.backend.domain.product

import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.stereotype.Repository

@Repository
interface ProductRepository : JpaRepository<Product, String> {
    fun findFirstByBarcode(barcode: String): Product?

}