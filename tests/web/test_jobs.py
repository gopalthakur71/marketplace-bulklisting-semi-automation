from src.web.jobs import JobStore, STEPS


def test_job_lifecycle():
    st = JobStore()
    job = st.create()
    assert job.status == "running"
    assert [s["name"] for s in job.steps] == STEPS
    assert all(s["state"] == "pending" for s in job.steps)

    st.set_step(job.id, "Ingest CSV", "done", count=7)
    fetched = st.get(job.id)
    ingest = next(s for s in fetched.steps if s["name"] == "Ingest CSV")
    assert ingest["state"] == "done"
    assert ingest["count"] == 7

    st.finish(job.id, {"filled": "out.xlsx", "products": 7})
    assert st.get(job.id).status == "done"
    assert st.get(job.id).result["products"] == 7


def test_fail_records_error():
    st = JobStore()
    job = st.create()
    st.fail(job.id, "boom")
    assert st.get(job.id).status == "error"
    assert st.get(job.id).error == "boom"


def test_request_cancel_sets_flag_without_ending_the_job():
    st = JobStore()
    job = st.create()
    assert job.cancel_requested is False
    st.request_cancel(job.id)
    assert st.get(job.id).cancel_requested is True
    # the worker is still winding down — status only changes once it actually stops
    assert st.get(job.id).status == "running"


def test_cancelled_is_distinct_from_error():
    st = JobStore()
    job = st.create()
    st.cancel(job.id)
    assert st.get(job.id).status == "cancelled"
    assert st.get(job.id).error is None


def test_request_cancel_unknown_job_is_a_no_op():
    JobStore().request_cancel("nope")   # a stale Stop click must not blow up


def test_get_unknown_returns_none():
    assert JobStore().get("nope") is None
