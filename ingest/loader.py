"""Load normalised PBS data into the database."""
from decimal import Decimal
from typing import Optional


async def upsert_medicines(conn, medicines: list[dict]) -> dict[str, str]:
    """Upsert medicines and return {ingredient_lower: id} mapping."""
    medicine_ids = {}
    for med in medicines:
        medicine_id = await conn.fetchval(
            """
            INSERT INTO medicines (ingredient, ingredient_lower, atc_code, therapeutic_group, therapeutic_subgroup)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ingredient_lower) DO UPDATE
              SET atc_code = EXCLUDED.atc_code,
                  therapeutic_group = EXCLUDED.therapeutic_group,
                  therapeutic_subgroup = EXCLUDED.therapeutic_subgroup,
                  updated_at = NOW()
            RETURNING id
            """,
            med["ingredient"],
            med["ingredient_lower"],
            med.get("atc_code"),
            med.get("therapeutic_group"),
            med.get("therapeutic_subgroup"),
        )
        medicine_ids[med["ingredient_lower"]] = str(medicine_id)
    return medicine_ids


async def insert_items(conn, items: list[dict], schedule_id: str, medicine_ids: dict[str, str]) -> dict[str, str]:
    """Insert items and return {pbs_code: item_id} mapping."""
    item_ids = {}
    for item in items:
        ingredient_lower = item.get("ingredient_lower", "")
        medicine_id = medicine_ids.get(ingredient_lower)
        if medicine_id is None:
            continue

        item_id = await conn.fetchval(
            """
            INSERT INTO items (
                pbs_code, schedule_id, medicine_id, brand_name, brand_name_lower,
                form, strength, pack_size, pack_unit, benefit_type, formulary,
                section, program_code, general_charge, concessional_charge,
                government_price, brand_premium, brand_premium_counts_to_safety_net,
                sixty_day_eligible, max_quantity, max_repeats, dangerous_drug,
                artg_id, sponsor, caution, biosimilar
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22,
                $23, $24, $25, $26
            )
            ON CONFLICT (pbs_code, schedule_id) DO UPDATE SET
                brand_name = EXCLUDED.brand_name,
                brand_name_lower = EXCLUDED.brand_name_lower,
                benefit_type = EXCLUDED.benefit_type,
                general_charge = EXCLUDED.general_charge,
                concessional_charge = EXCLUDED.concessional_charge,
                government_price = EXCLUDED.government_price,
                brand_premium = EXCLUDED.brand_premium,
                sixty_day_eligible = EXCLUDED.sixty_day_eligible,
                artg_id = EXCLUDED.artg_id,
                sponsor = EXCLUDED.sponsor,
                caution = EXCLUDED.caution,
                biosimilar = EXCLUDED.biosimilar
            RETURNING id
            """,
            item["pbs_code"],
            schedule_id,
            medicine_id,
            item["brand_name"],
            item["brand_name_lower"],
            item.get("form"),
            item.get("strength"),
            item.get("pack_size"),
            item.get("pack_unit"),
            item["benefit_type"],
            item.get("formulary"),
            item.get("section"),
            item.get("program_code"),
            item.get("general_charge"),
            item.get("concessional_charge"),
            item.get("government_price"),
            item.get("brand_premium", Decimal("0.00")),
            item.get("brand_premium_counts_to_safety_net", False),
            item.get("sixty_day_eligible", False),
            item.get("max_quantity"),
            item.get("max_repeats"),
            item.get("dangerous_drug", False),
            item.get("artg_id"),
            item.get("sponsor"),
            item.get("caution"),
            item.get("biosimilar", False),
        )
        item_ids[item["pbs_code"]] = str(item_id)
    return item_ids


async def insert_restrictions(conn, items: list[dict], item_ids: dict[str, str]):
    """Insert restrictions for each item."""
    for item in items:
        pbs_code = item["pbs_code"]
        item_id = item_ids.get(pbs_code)
        if item_id is None:
            continue
        await conn.execute("DELETE FROM restrictions WHERE item_id = $1", item_id)
        for r in item.get("restrictions", []):
            await conn.execute(
                """
                INSERT INTO restrictions (
                    item_id, restriction_code, restriction_type, streamlined_code,
                    indication, restriction_text, prescriber_type,
                    authority_required, continuation_only, clinical_criteria
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                item_id,
                r.get("restriction_code"),
                r.get("restriction_type"),
                r.get("streamlined_code"),
                r.get("indication"),
                r.get("restriction_text"),
                r.get("prescriber_type"),
                r.get("authority_required", False),
                r.get("continuation_only", False),
                r.get("clinical_criteria"),
            )


async def insert_changes(conn, changes: list[dict], schedule_id: str):
    """Insert change records."""
    for change in changes:
        await conn.execute(
            """
            INSERT INTO changes (schedule_id, pbs_code, change_type, field_name, old_value, new_value)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            schedule_id,
            change["pbs_code"],
            change["change_type"],
            change.get("field_name"),
            change.get("old_value"),
            change.get("new_value"),
        )


async def insert_fees(conn, fees: list[dict], schedule_id: str):
    """Insert fee records for a schedule."""
    if not fees:
        return
    await conn.execute("DELETE FROM fees WHERE schedule_id = $1", schedule_id)
    for fee in fees:
        await conn.execute(
            """
            INSERT INTO fees (
                schedule_id, fee_code, fee_type, description, amount, patient_contribution,
                dispensing_fee_ready_prepared, dispensing_fee_dangerous_drug,
                dispensing_fee_extra, dispensing_fee_extemporaneous,
                safety_net_recording_fee_ep, safety_net_recording_fee_rp,
                dispensing_fee_water_added, container_fee_injectable, container_fee_other,
                gnrl_copay_discount_general, gnrl_copay_discount_hospital,
                con_copay_discount_general, con_copay_discount_hospital,
                efc_diluent_fee, efc_preparation_fee, efc_distribution_fee,
                acss_imdq60_payment, acss_payment
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24)
            ON CONFLICT (fee_code, schedule_id) DO UPDATE SET
                fee_type = EXCLUDED.fee_type,
                description = EXCLUDED.description,
                amount = EXCLUDED.amount,
                patient_contribution = EXCLUDED.patient_contribution,
                dispensing_fee_ready_prepared = EXCLUDED.dispensing_fee_ready_prepared,
                dispensing_fee_dangerous_drug = EXCLUDED.dispensing_fee_dangerous_drug,
                dispensing_fee_extra = EXCLUDED.dispensing_fee_extra,
                dispensing_fee_extemporaneous = EXCLUDED.dispensing_fee_extemporaneous,
                safety_net_recording_fee_ep = EXCLUDED.safety_net_recording_fee_ep,
                safety_net_recording_fee_rp = EXCLUDED.safety_net_recording_fee_rp,
                dispensing_fee_water_added = EXCLUDED.dispensing_fee_water_added,
                container_fee_injectable = EXCLUDED.container_fee_injectable,
                container_fee_other = EXCLUDED.container_fee_other,
                gnrl_copay_discount_general = EXCLUDED.gnrl_copay_discount_general,
                gnrl_copay_discount_hospital = EXCLUDED.gnrl_copay_discount_hospital,
                con_copay_discount_general = EXCLUDED.con_copay_discount_general,
                con_copay_discount_hospital = EXCLUDED.con_copay_discount_hospital,
                efc_diluent_fee = EXCLUDED.efc_diluent_fee,
                efc_preparation_fee = EXCLUDED.efc_preparation_fee,
                efc_distribution_fee = EXCLUDED.efc_distribution_fee,
                acss_imdq60_payment = EXCLUDED.acss_imdq60_payment,
                acss_payment = EXCLUDED.acss_payment
            """,
            schedule_id,
            fee.get("fee_code", ""),
            fee.get("fee_type"),
            fee.get("description"),
            fee.get("amount"),
            fee.get("patient_contribution"),
            fee.get("dispensing_fee_ready_prepared"),
            fee.get("dispensing_fee_dangerous_drug"),
            fee.get("dispensing_fee_extra"),
            fee.get("dispensing_fee_extemporaneous"),
            fee.get("safety_net_recording_fee_ep"),
            fee.get("safety_net_recording_fee_rp"),
            fee.get("dispensing_fee_water_added"),
            fee.get("container_fee_injectable"),
            fee.get("container_fee_other"),
            fee.get("gnrl_copay_discount_general"),
            fee.get("gnrl_copay_discount_hospital"),
            fee.get("con_copay_discount_general"),
            fee.get("con_copay_discount_hospital"),
            fee.get("efc_diluent_fee"),
            fee.get("efc_preparation_fee"),
            fee.get("efc_distribution_fee"),
            fee.get("acss_imdq60_payment"),
            fee.get("acss_payment"),
        )


async def insert_prescribing_texts(conn, prescribing_texts: list[dict], schedule_id: str):
    """Insert prescribing text records for a schedule."""
    if not prescribing_texts:
        return
    await conn.execute("DELETE FROM prescribing_texts WHERE schedule_id = $1", schedule_id)
    for pt in prescribing_texts:
        await conn.execute(
            """
            INSERT INTO prescribing_texts (schedule_id, prescribing_text_id, text_type, complex_authority_required, prescribing_txt)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (prescribing_text_id, schedule_id) DO UPDATE SET
                text_type = EXCLUDED.text_type,
                complex_authority_required = EXCLUDED.complex_authority_required,
                prescribing_txt = EXCLUDED.prescribing_txt
            """,
            schedule_id,
            pt.get("prescribing_text_id", ""),
            pt.get("text_type"),
            pt.get("complex_authority_required", False),
            pt.get("prescribing_txt"),
        )


async def insert_indications(conn, indications: list[dict], schedule_id: str):
    """Insert indication records for a schedule."""
    if not indications:
        return
    await conn.execute("DELETE FROM indications WHERE schedule_id = $1", schedule_id)
    for ind in indications:
        await conn.execute(
            """
            INSERT INTO indications (schedule_id, indication_id, pbs_code, indication_text, condition_description)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (indication_id, schedule_id) DO UPDATE SET
                pbs_code = EXCLUDED.pbs_code,
                indication_text = EXCLUDED.indication_text,
                condition_description = EXCLUDED.condition_description
            """,
            schedule_id,
            ind.get("indication_id", ""),
            ind.get("pbs_code"),
            ind.get("indication_text"),
            ind.get("condition_description"),
        )


async def insert_amt_items(conn, amt_items: list[dict], schedule_id: str):
    """Insert AMT/ATC classification records for a schedule."""
    if not amt_items:
        return
    await conn.execute("DELETE FROM amt_items WHERE schedule_id = $1", schedule_id)
    for amt in amt_items:
        await conn.execute(
            """
            INSERT INTO amt_items (
                schedule_id, amt_id, pbs_concept_id, concept_type,
                preferred_term, atc_code, parent_amt_id, exempt_ind
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (amt_id, schedule_id) DO UPDATE SET
                pbs_concept_id = EXCLUDED.pbs_concept_id,
                concept_type = EXCLUDED.concept_type,
                preferred_term = EXCLUDED.preferred_term,
                atc_code = EXCLUDED.atc_code,
                parent_amt_id = EXCLUDED.parent_amt_id,
                exempt_ind = EXCLUDED.exempt_ind
            """,
            schedule_id,
            amt.get("amt_id", ""),
            amt.get("pbs_concept_id"),
            amt.get("concept_type"),
            amt.get("preferred_term"),
            amt.get("atc_code"),
            amt.get("parent_amt_id"),
            amt.get("exempt_ind"),
        )


async def insert_item_amt_relationships(conn, relationships: list[dict], schedule_id: str):
    """Insert item <-> AMT concept relationships."""
    if not relationships:
        return
    await conn.execute("DELETE FROM item_amt_relationships WHERE schedule_id = $1", schedule_id)
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO item_amt_relationships (schedule_id, pbs_code, amt_id, relationship_type)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (pbs_code, amt_id, schedule_id) DO NOTHING
            """,
            schedule_id,
            rel.get("pbs_code", ""),
            rel.get("amt_id", ""),
            rel.get("relationship_type"),
        )


async def insert_item_dispensing_rules(conn, rules: list[dict], schedule_id: str):
    """Insert item <-> dispensing rule links."""
    if not rules:
        return
    await conn.execute("DELETE FROM item_dispensing_rules WHERE schedule_id = $1", schedule_id)
    for rule in rules:
        await conn.execute(
            """
            INSERT INTO item_dispensing_rules (schedule_id, pbs_code, rule_code)
            VALUES ($1, $2, $3)
            ON CONFLICT (pbs_code, rule_code, schedule_id) DO NOTHING
            """,
            schedule_id,
            rule.get("pbs_code", ""),
            rule.get("rule_code", ""),
        )


async def insert_program_dispensing_rules(conn, rules: list[dict], schedule_id: str):
    """Insert program dispensing rules."""
    if not rules:
        return
    await conn.execute("DELETE FROM program_dispensing_rules WHERE schedule_id = $1", schedule_id)
    for rule in rules:
        await conn.execute(
            """
            INSERT INTO program_dispensing_rules (
                schedule_id, program_code, rule_code,
                dispensing_rule_reference, community_pharmacy_indicator,
                dispensing_quantity, dispensing_unit, repeats_allowed, description
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (rule_code, schedule_id) DO UPDATE SET
                program_code = EXCLUDED.program_code,
                dispensing_rule_reference = EXCLUDED.dispensing_rule_reference,
                community_pharmacy_indicator = EXCLUDED.community_pharmacy_indicator,
                dispensing_quantity = EXCLUDED.dispensing_quantity,
                dispensing_unit = EXCLUDED.dispensing_unit,
                repeats_allowed = EXCLUDED.repeats_allowed,
                description = EXCLUDED.description
            """,
            schedule_id,
            rule.get("program_code"),  # NULL from real API
            rule.get("rule_code", ""),
            rule.get("dispensing_rule_reference"),
            rule.get("community_pharmacy_indicator"),
            rule.get("dispensing_quantity"),
            rule.get("dispensing_unit"),
            rule.get("repeats_allowed"),
            rule.get("description"),
        )


async def insert_item_restriction_relationships(conn, relationships: list[dict], schedule_id: str):
    """Insert item <-> restriction relationships."""
    if not relationships:
        return
    await conn.execute("DELETE FROM item_restriction_relationships WHERE schedule_id = $1", schedule_id)
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO item_restriction_relationships (schedule_id, pbs_code, restriction_code)
            VALUES ($1, $2, $3)
            ON CONFLICT (pbs_code, restriction_code, schedule_id) DO NOTHING
            """,
            schedule_id,
            rel.get("pbs_code", ""),
            rel.get("restriction_code", ""),
        )


async def insert_restriction_prescribing_text_relationships(conn, relationships: list[dict], schedule_id: str):
    """Insert restriction <-> prescribing text relationships."""
    if not relationships:
        return
    await conn.execute(
        "DELETE FROM restriction_prescribing_text_relationships WHERE schedule_id = $1", schedule_id
    )
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO restriction_prescribing_text_relationships (schedule_id, restriction_code, prescribing_text_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (restriction_code, prescribing_text_id, schedule_id) DO NOTHING
            """,
            schedule_id,
            rel.get("restriction_code", ""),
            rel.get("prescribing_text_id", ""),
        )


async def insert_item_prescribing_text_relationships(conn, relationships: list[dict], schedule_id: str):
    """Insert item <-> prescribing text relationships."""
    if not relationships:
        return
    await conn.execute(
        "DELETE FROM item_prescribing_text_relationships WHERE schedule_id = $1", schedule_id
    )
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO item_prescribing_text_relationships (schedule_id, pbs_code, prescribing_text_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (pbs_code, prescribing_text_id, schedule_id) DO NOTHING
            """,
            schedule_id,
            rel.get("pbs_code", ""),
            rel.get("prescribing_text_id", ""),
        )


async def insert_summary_of_changes(conn, changes: list[dict], schedule_id: str):
    """Insert official PBS summary of changes records."""
    if not changes:
        return
    await conn.execute("DELETE FROM summary_of_changes WHERE schedule_id = $1", schedule_id)
    for chg in changes:
        await conn.execute(
            """
            INSERT INTO summary_of_changes (schedule_id, pbs_code, change_type, effective_date, description, section)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            schedule_id,
            chg.get("pbs_code"),
            chg.get("change_type"),
            chg.get("effective_date"),
            chg.get("description"),
            chg.get("section"),
        )


async def insert_organisations(conn, organisations: list[dict], schedule_id: str):
    """Insert organisation (manufacturer/sponsor) records for a schedule."""
    if not organisations:
        return
    await conn.execute("DELETE FROM organisations WHERE schedule_id = $1", schedule_id)
    for org in organisations:
        await conn.execute(
            """
            INSERT INTO organisations (
                schedule_id, organisation_id, name, abn,
                street_address, city, state, postcode
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (organisation_id, schedule_id) DO UPDATE SET
                name = EXCLUDED.name,
                abn = EXCLUDED.abn,
                street_address = EXCLUDED.street_address,
                city = EXCLUDED.city,
                state = EXCLUDED.state,
                postcode = EXCLUDED.postcode
            """,
            schedule_id,
            org.get("organisation_id"),
            org.get("name"),
            org.get("abn"),
            org.get("street_address"),
            org.get("city"),
            org.get("state"),
            org.get("postcode"),
        )


async def insert_programs(conn, programs: list[dict], schedule_id: str):
    """Insert PBS program records for a schedule."""
    if not programs:
        return
    await conn.execute("DELETE FROM programs WHERE schedule_id = $1", schedule_id)
    for prog in programs:
        await conn.execute(
            """
            INSERT INTO programs (schedule_id, program_code, program_title)
            VALUES ($1, $2, $3)
            ON CONFLICT (program_code, schedule_id) DO UPDATE SET
                program_title = EXCLUDED.program_title
            """,
            schedule_id,
            prog.get("program_code", ""),
            prog.get("program_title"),
        )


async def insert_atc_codes(conn, atc_codes: list[dict], schedule_id: str):
    """Insert ATC classification codes for a schedule."""
    if not atc_codes:
        return
    await conn.execute("DELETE FROM atc_codes WHERE schedule_id = $1", schedule_id)
    for atc in atc_codes:
        await conn.execute(
            """
            INSERT INTO atc_codes (schedule_id, atc_code, atc_description, atc_level, atc_parent_code)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (atc_code, schedule_id) DO UPDATE SET
                atc_description = EXCLUDED.atc_description,
                atc_level = EXCLUDED.atc_level,
                atc_parent_code = EXCLUDED.atc_parent_code
            """,
            schedule_id,
            atc.get("atc_code", ""),
            atc.get("atc_description"),
            atc.get("atc_level"),
            atc.get("atc_parent_code"),
        )


async def insert_item_atc_relationships(conn, relationships: list[dict], schedule_id: str):
    """Insert item <-> ATC code relationships."""
    if not relationships:
        return
    await conn.execute("DELETE FROM item_atc_relationships WHERE schedule_id = $1", schedule_id)
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO item_atc_relationships (schedule_id, pbs_code, atc_code, atc_priority_pct)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (pbs_code, atc_code, schedule_id) DO UPDATE SET
                atc_priority_pct = EXCLUDED.atc_priority_pct
            """,
            schedule_id,
            rel.get("pbs_code", ""),
            rel.get("atc_code", ""),
            rel.get("atc_priority_pct"),
        )


async def insert_copayments(conn, copayments: Optional[dict], schedule_id: str):
    """Insert schedule-level copayment thresholds."""
    if not copayments:
        return
    await conn.execute(
        """
        INSERT INTO copayments (
            schedule_id, general, concessional,
            safety_net_general, safety_net_concessional,
            safety_net_card_issue, increased_discount_limit,
            safety_net_ctg_contribution
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (schedule_id) DO UPDATE SET
            general = EXCLUDED.general,
            concessional = EXCLUDED.concessional,
            safety_net_general = EXCLUDED.safety_net_general,
            safety_net_concessional = EXCLUDED.safety_net_concessional,
            safety_net_card_issue = EXCLUDED.safety_net_card_issue,
            increased_discount_limit = EXCLUDED.increased_discount_limit,
            safety_net_ctg_contribution = EXCLUDED.safety_net_ctg_contribution
        """,
        schedule_id,
        copayments.get("general"),
        copayments.get("concessional"),
        copayments.get("safety_net_general"),
        copayments.get("safety_net_concessional"),
        copayments.get("safety_net_card_issue"),
        copayments.get("increased_discount_limit"),
        copayments.get("safety_net_ctg_contribution"),
    )


async def insert_item_pricing(conn, item_pricing: list[dict], schedule_id: str):
    """Insert item-level dispensing pricing records."""
    if not item_pricing:
        return
    await conn.execute("DELETE FROM item_pricing WHERE schedule_id = $1", schedule_id)
    for p in item_pricing:
        await conn.execute(
            """
            INSERT INTO item_pricing (
                schedule_id, li_item_id, pbs_code, dispensing_rule_mnem,
                brand_premium, commonwealth_price, max_general_patient_charge,
                special_patient_contribution, fee_dispensing, fee_dispensing_dangerous_drug,
                fee_container_other, fee_container_injectable
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (li_item_id, dispensing_rule_mnem, schedule_id) DO UPDATE SET
                pbs_code = EXCLUDED.pbs_code,
                brand_premium = EXCLUDED.brand_premium,
                commonwealth_price = EXCLUDED.commonwealth_price,
                max_general_patient_charge = EXCLUDED.max_general_patient_charge,
                special_patient_contribution = EXCLUDED.special_patient_contribution,
                fee_dispensing = EXCLUDED.fee_dispensing,
                fee_dispensing_dangerous_drug = EXCLUDED.fee_dispensing_dangerous_drug,
                fee_container_other = EXCLUDED.fee_container_other,
                fee_container_injectable = EXCLUDED.fee_container_injectable
            """,
            schedule_id,
            p.get("li_item_id", ""),
            p.get("pbs_code"),
            p.get("dispensing_rule_mnem"),
            p.get("brand_premium", Decimal("0.00")),
            p.get("commonwealth_price"),
            p.get("max_general_patient_charge"),
            p.get("special_patient_contribution"),
            p.get("fee_dispensing"),
            p.get("fee_dispensing_dangerous_drug"),
            p.get("fee_container_other"),
            p.get("fee_container_injectable"),
        )


async def insert_item_organisation_relationships(conn, relationships: list[dict], schedule_id: str):
    """Insert item <-> organisation relationships."""
    if not relationships:
        return
    await conn.execute("DELETE FROM item_organisation_relationships WHERE schedule_id = $1", schedule_id)
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO item_organisation_relationships (schedule_id, pbs_code, organisation_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (pbs_code, organisation_id, schedule_id) DO NOTHING
            """,
            schedule_id,
            rel.get("pbs_code", ""),
            rel.get("organisation_id"),
        )


async def insert_containers(conn, containers: list[dict], schedule_id: str):
    if not containers:
        return
    await conn.execute("DELETE FROM containers WHERE schedule_id = $1", schedule_id)
    for c in containers:
        await conn.execute(
            """
            INSERT INTO containers (
                schedule_id, container_code, container_name, mark_up, agreed_purchasing_unit,
                average_exact_unit_price, average_rounded_unit_price, container_type,
                container_quantity, container_unit_of_measure
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (container_code, schedule_id) DO UPDATE SET
                container_name = EXCLUDED.container_name,
                mark_up = EXCLUDED.mark_up
            """,
            schedule_id,
            c.get("container_code", ""),
            c.get("container_name"),
            c.get("mark_up"),
            c.get("agreed_purchasing_unit"),
            c.get("average_exact_unit_price"),
            c.get("average_rounded_unit_price"),
            c.get("container_type"),
            c.get("container_quantity"),
            c.get("container_unit_of_measure"),
        )


async def insert_container_organisation_relationships(conn, relationships: list[dict], schedule_id: str):
    if not relationships:
        return
    await conn.execute("DELETE FROM container_organisation_relationships WHERE schedule_id = $1", schedule_id)
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO container_organisation_relationships (schedule_id, container_code, organisation_id)
            VALUES ($1,$2,$3)
            ON CONFLICT (container_code, organisation_id, schedule_id) DO NOTHING
            """,
            schedule_id, rel.get("container_code", ""), rel.get("organisation_id", ""),
        )


async def insert_criteria(conn, criteria: list[dict], schedule_id: str):
    if not criteria:
        return
    await conn.execute("DELETE FROM criteria WHERE schedule_id = $1", schedule_id)
    for c in criteria:
        await conn.execute(
            """
            INSERT INTO criteria (schedule_id, criteria_id, criteria_type, parameter_relationship)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (criteria_id, schedule_id) DO UPDATE SET
                criteria_type = EXCLUDED.criteria_type,
                parameter_relationship = EXCLUDED.parameter_relationship
            """,
            schedule_id, c.get("criteria_id", ""), c.get("criteria_type"), c.get("parameter_relationship"),
        )


async def insert_criteria_parameter_relationships(conn, relationships: list[dict], schedule_id: str):
    if not relationships:
        return
    await conn.execute("DELETE FROM criteria_parameter_relationships WHERE schedule_id = $1", schedule_id)
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO criteria_parameter_relationships (schedule_id, criteria_id, parameter_id, pt_position)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (criteria_id, parameter_id, schedule_id) DO NOTHING
            """,
            schedule_id, rel.get("criteria_id", ""), rel.get("parameter_id", ""), rel.get("pt_position"),
        )


async def insert_parameters(conn, parameters: list[dict], schedule_id: str):
    if not parameters:
        return
    await conn.execute("DELETE FROM parameters WHERE schedule_id = $1", schedule_id)
    for p in parameters:
        await conn.execute(
            """
            INSERT INTO parameters (schedule_id, parameter_id, assessment_type, parameter_type)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (parameter_id, schedule_id) DO UPDATE SET
                assessment_type = EXCLUDED.assessment_type,
                parameter_type = EXCLUDED.parameter_type
            """,
            schedule_id, p.get("parameter_id", ""), p.get("assessment_type"), p.get("parameter_type"),
        )


async def insert_item_prescribers(conn, prescribers: list[dict], schedule_id: str):
    if not prescribers:
        return
    await conn.execute("DELETE FROM item_prescribers WHERE schedule_id = $1", schedule_id)
    for p in prescribers:
        await conn.execute(
            """
            INSERT INTO item_prescribers (schedule_id, pbs_code, prescriber_code, prescriber_type)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (pbs_code, prescriber_code, schedule_id) DO NOTHING
            """,
            schedule_id, p.get("pbs_code", ""), p.get("prescriber_code", ""), p.get("prescriber_type"),
        )


async def insert_markup_bands(conn, markup_bands: list[dict], schedule_id: str):
    if not markup_bands:
        return
    await conn.execute("DELETE FROM markup_bands WHERE schedule_id = $1", schedule_id)
    for mb in markup_bands:
        await conn.execute(
            """
            INSERT INTO markup_bands (
                schedule_id, markup_band_code, program_code, dispensing_rule_mnem,
                limit_amount, variable_rate, offset_amount, fixed_amount
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            schedule_id,
            mb.get("markup_band_code", ""),
            mb.get("program_code"),
            mb.get("dispensing_rule_mnem"),
            mb.get("limit_amount"),
            mb.get("variable_rate"),
            mb.get("offset_amount"),
            mb.get("fixed_amount"),
        )


async def insert_item_pricing_events(conn, events: list[dict], schedule_id: str):
    if not events:
        return
    await conn.execute("DELETE FROM item_pricing_events WHERE schedule_id = $1", schedule_id)
    for e in events:
        await conn.execute(
            """
            INSERT INTO item_pricing_events (schedule_id, pbs_code, event_type, effective_date, previous_price, new_price)
            VALUES ($1,$2,$3,$4,$5,$6)
            """,
            schedule_id,
            e.get("pbs_code"),
            e.get("event_type"),
            e.get("effective_date"),
            e.get("previous_price"),
            e.get("new_price"),
        )


async def insert_extemporaneous_ingredients(conn, ingredients: list[dict], schedule_id: str):
    if not ingredients:
        return
    await conn.execute("DELETE FROM extemporaneous_ingredients WHERE schedule_id = $1", schedule_id)
    for i in ingredients:
        await conn.execute(
            """
            INSERT INTO extemporaneous_ingredients (
                schedule_id, pbs_code, agreed_purchasing_unit,
                exact_tenth_gram_per_ml_price, exact_one_gram_per_ml_price,
                exact_ten_gram_per_ml_price, exact_hundred_gram_per_ml_price,
                rounded_tenth_gram_per_ml_price, rounded_one_gram_per_ml_price,
                rounded_ten_gram_per_ml_price, rounded_hundred_gram_per_ml_price
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (pbs_code, schedule_id) DO UPDATE SET
                agreed_purchasing_unit = EXCLUDED.agreed_purchasing_unit
            """,
            schedule_id,
            i.get("pbs_code", ""),
            i.get("agreed_purchasing_unit"),
            i.get("exact_tenth_gram_per_ml_price"),
            i.get("exact_one_gram_per_ml_price"),
            i.get("exact_ten_gram_per_ml_price"),
            i.get("exact_hundred_gram_per_ml_price"),
            i.get("rounded_tenth_gram_per_ml_price"),
            i.get("rounded_one_gram_per_ml_price"),
            i.get("rounded_ten_gram_per_ml_price"),
            i.get("rounded_hundred_gram_per_ml_price"),
        )


async def insert_extemporaneous_preparations(conn, preparations: list[dict], schedule_id: str):
    if not preparations:
        return
    await conn.execute("DELETE FROM extemporaneous_preparations WHERE schedule_id = $1", schedule_id)
    for p in preparations:
        await conn.execute(
            """
            INSERT INTO extemporaneous_preparations (schedule_id, pbs_code, preparation, maximum_quantity, maximum_quantity_unit)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (pbs_code, schedule_id) DO UPDATE SET
                preparation = EXCLUDED.preparation
            """,
            schedule_id,
            p.get("pbs_code", ""),
            p.get("preparation"),
            p.get("maximum_quantity"),
            p.get("maximum_quantity_unit"),
        )


async def insert_extemporaneous_prep_sfp_relationships(conn, relationships: list[dict], schedule_id: str):
    if not relationships:
        return
    await conn.execute("DELETE FROM extemporaneous_prep_sfp_relationships WHERE schedule_id = $1", schedule_id)
    for rel in relationships:
        await conn.execute(
            """
            INSERT INTO extemporaneous_prep_sfp_relationships (schedule_id, sfp_pbs_code, ex_prep_pbs_code)
            VALUES ($1,$2,$3)
            ON CONFLICT (sfp_pbs_code, ex_prep_pbs_code, schedule_id) DO NOTHING
            """,
            schedule_id, rel.get("sfp_pbs_code", ""), rel.get("ex_prep_pbs_code", ""),
        )


async def insert_extemporaneous_tariffs(conn, tariffs: list[dict], schedule_id: str):
    if not tariffs:
        return
    await conn.execute("DELETE FROM extemporaneous_tariffs WHERE schedule_id = $1", schedule_id)
    for t in tariffs:
        await conn.execute(
            """
            INSERT INTO extemporaneous_tariffs (
                schedule_id, pbs_code, drug_name, agreed_purchasing_unit, markup,
                rounded_rec_one_tenth_gram, rounded_rec_one_gram, rounded_rec_ten_gram, rounded_rec_hundred_gram,
                exact_rec_one_tenth_gram, exact_rec_one_gram, exact_rec_ten_gram, exact_rec_hundred_gram
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (pbs_code, schedule_id) DO UPDATE SET
                markup = EXCLUDED.markup
            """,
            schedule_id,
            t.get("pbs_code", ""),
            t.get("drug_name"),
            t.get("agreed_purchasing_unit"),
            t.get("markup"),
            t.get("rounded_rec_one_tenth_gram"),
            t.get("rounded_rec_one_gram"),
            t.get("rounded_rec_ten_gram"),
            t.get("rounded_rec_hundred_gram"),
            t.get("exact_rec_one_tenth_gram"),
            t.get("exact_rec_one_gram"),
            t.get("exact_rec_ten_gram"),
            t.get("exact_rec_hundred_gram"),
        )


async def insert_standard_formula_preparations(conn, preparations: list[dict], schedule_id: str):
    if not preparations:
        return
    await conn.execute("DELETE FROM standard_formula_preparations WHERE schedule_id = $1", schedule_id)
    for s in preparations:
        await conn.execute(
            """
            INSERT INTO standard_formula_preparations (
                schedule_id, pbs_code, sfp_drug_name, sfp_reference,
                container_fee, dispensing_fee_max_quantity, safety_net_price,
                maximum_patient_charge, maximum_quantity_unit, maximum_quantity
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (pbs_code, schedule_id) DO UPDATE SET
                sfp_drug_name = EXCLUDED.sfp_drug_name
            """,
            schedule_id,
            s.get("pbs_code", ""),
            s.get("sfp_drug_name"),
            s.get("sfp_reference"),
            s.get("container_fee"),
            s.get("dispensing_fee_max_quantity"),
            s.get("safety_net_price"),
            s.get("maximum_patient_charge"),
            s.get("maximum_quantity_unit"),
            s.get("maximum_quantity"),
        )


async def deactivate_removed_medicines(conn, schedule_id: str):
    """Mark medicines as inactive if they have no items in the new schedule."""
    await conn.execute(
        """
        UPDATE medicines SET is_active = FALSE
        WHERE id NOT IN (
            SELECT DISTINCT medicine_id FROM items WHERE schedule_id = $1
        )
        """,
        schedule_id,
    )


async def update_schedule_counts(conn, schedule_id: str, item_count: int, change_count: int):
    """Update item and change counts on the schedule."""
    await conn.execute(
        "UPDATE schedules SET item_count = $1, change_count = $2 WHERE id = $3",
        item_count, change_count, schedule_id,
    )


async def load_to_database(pool, month: str, normalised: dict, changes: list[dict]):
    """Load all normalised PBS data to the database."""
    async with pool.acquire() as conn:
        schedule_id = await conn.fetchval(
            "SELECT id FROM schedules WHERE month = $1", month
        )
        if schedule_id is None:
            raise ValueError(f"Schedule for month {month} not found in database")

        schedule_id = str(schedule_id)

        # Core data
        medicine_ids = await upsert_medicines(conn, normalised["medicines"])
        item_ids = await insert_items(conn, normalised["items"], schedule_id, medicine_ids)
        await insert_restrictions(conn, normalised["items"], item_ids)
        await insert_changes(conn, changes, schedule_id)

        # Expanded data — all safe with empty lists when not provided
        await insert_fees(conn, normalised.get("fees", []), schedule_id)
        await insert_prescribing_texts(conn, normalised.get("prescribing_texts", []), schedule_id)
        await insert_indications(conn, normalised.get("indications", []), schedule_id)
        await insert_amt_items(conn, normalised.get("amt_items", []), schedule_id)
        await insert_item_amt_relationships(conn, normalised.get("item_amt_relationships", []), schedule_id)
        await insert_item_dispensing_rules(conn, normalised.get("item_dispensing_rules", []), schedule_id)
        await insert_program_dispensing_rules(conn, normalised.get("program_dispensing_rules", []), schedule_id)
        await insert_item_restriction_relationships(conn, normalised.get("item_restriction_relationships", []), schedule_id)
        await insert_restriction_prescribing_text_relationships(conn, normalised.get("restriction_prescribing_text_relationships", []), schedule_id)
        await insert_item_prescribing_text_relationships(conn, normalised.get("item_prescribing_text_relationships", []), schedule_id)
        await insert_summary_of_changes(conn, normalised.get("summary_of_changes", []), schedule_id)

        # Reference data
        await insert_organisations(conn, normalised.get("organisations", []), schedule_id)
        await insert_programs(conn, normalised.get("programs", []), schedule_id)
        await insert_atc_codes(conn, normalised.get("atc_codes", []), schedule_id)
        await insert_item_atc_relationships(conn, normalised.get("item_atc_relationships", []), schedule_id)
        await insert_copayments(conn, normalised.get("copayments"), schedule_id)
        await insert_item_pricing(conn, normalised.get("item_pricing", []), schedule_id)
        await insert_item_organisation_relationships(conn, normalised.get("item_organisation_relationships", []), schedule_id)

        # New endpoints (migration 007)
        await insert_containers(conn, normalised.get("containers", []), schedule_id)
        await insert_container_organisation_relationships(conn, normalised.get("container_organisation_relationships", []), schedule_id)
        await insert_criteria(conn, normalised.get("criteria", []), schedule_id)
        await insert_criteria_parameter_relationships(conn, normalised.get("criteria_parameter_relationships", []), schedule_id)
        await insert_parameters(conn, normalised.get("parameters", []), schedule_id)
        await insert_item_prescribers(conn, normalised.get("item_prescribers", []), schedule_id)
        await insert_markup_bands(conn, normalised.get("markup_bands", []), schedule_id)
        await insert_item_pricing_events(conn, normalised.get("item_pricing_events", []), schedule_id)
        await insert_extemporaneous_ingredients(conn, normalised.get("extemporaneous_ingredients", []), schedule_id)
        await insert_extemporaneous_preparations(conn, normalised.get("extemporaneous_preparations", []), schedule_id)
        await insert_extemporaneous_prep_sfp_relationships(conn, normalised.get("extemporaneous_prep_sfp_relationships", []), schedule_id)
        await insert_extemporaneous_tariffs(conn, normalised.get("extemporaneous_tariffs", []), schedule_id)
        await insert_standard_formula_preparations(conn, normalised.get("standard_formula_preparations", []), schedule_id)

        await update_schedule_counts(conn, schedule_id, len(item_ids), len(changes))
