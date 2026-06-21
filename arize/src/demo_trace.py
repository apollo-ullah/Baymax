"""Demo trace runner.

End-to-end local trace demo that simulates the four trace events. Does not
connect to Phoenix or use OpenTelemetry yet.
"""

try:
    from .phoenix_client import print_phoenix_status
    from .trace_inventory import trace_inventory_low
    from .trace_forecast import trace_forecast_signal
    from .trace_reasoning import trace_reasoning_decision
    from .trace_transfer import trace_transfer_recommendation
    from .trace_decision_chain import trace_decision_chain
    from .trace_decision_outcome import trace_decision_outcome
except ImportError:
    from arize.src.phoenix_client import print_phoenix_status
    from arize.src.trace_inventory import trace_inventory_low
    from arize.src.trace_forecast import trace_forecast_signal
    from arize.src.trace_reasoning import trace_reasoning_decision
    from arize.src.trace_transfer import trace_transfer_recommendation
    from arize.src.trace_decision_chain import trace_decision_chain
    from arize.src.trace_decision_outcome import trace_decision_outcome


def main():
    """Run the end-to-end local trace demo."""
    print("Arize Observability Demo")

    print_phoenix_status()

    trace_inventory_low(
        hospital_id="hospital_a",
        item="IV Fluids",
        current_pct=20,
        status="low",
    )

    trace_forecast_signal(
        region="san_francisco",
        item="IV Fluids",
        predicted_demand_increase_pct=40,
        reason="Respiratory illness spike and cold front",
    )

    trace_reasoning_decision(
        hospital_id="hospital_a",
        item="IV Fluids",
        severity="critical",
        recommended_action="transfer_from_nearby_hospital",
        reasoning=(
            "Inventory is below safety threshold while forecast demand is rising. "
            "Recommend transfer before shortage worsens."
        ),
        source_hospital="hospital_b",
        transfer_quantity=150,
    )

    trace_transfer_recommendation(
        source_hospital="hospital_b",
        destination_hospital="hospital_a",
        item="IV Fluids",
        transfer_quantity=150,
        eta_minutes=18,
        severity="critical",
    )

    trace_decision_chain(
        hospital_id="hospital_a",
        item="IV Fluids",
        inventory_signal_strength=0.90,
        forecast_signal_strength=0.80,
        decision_confidence=0.87,
        recommended_action="transfer_from_nearby_hospital",
        source_hospital="hospital_b",
        transfer_quantity=150,
    )

    trace_decision_outcome(
        hospital_id="hospital_a",
        item="IV Fluids",
        recommended_quantity=150,
        actual_quantity=150,
        outcome="successful_transfer",
        improvement_note=(
            "Recommendation matched fulfilled transfer; keep current confidence "
            "threshold for similar future cases."
        ),
    )

    print(
        "Trace demo complete. These events show what Arize would make observable: "
        "inventory signal, forecast signal, reasoning decision, transfer "
        "recommendation, decision confidence, and outcome feedback."
    )


if __name__ == "__main__":
    main()
