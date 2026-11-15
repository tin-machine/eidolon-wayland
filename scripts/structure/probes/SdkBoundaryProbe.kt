package io.github.tinmachine.eidolon

import android.os.Handler
import com.meta.spatial.core.DataModel
import com.meta.spatial.core.Entity
import com.meta.spatial.runtime.Scene
import com.meta.spatial.runtime.SceneObject
import java.util.concurrent.CompletableFuture

private class Fixture {
    val evidence = SdkEvidenceBridge()
    val lease = SdkEvidenceBridge.Lease(11, 7, 9, 2, 1, 4, 3)
    val scene = Scene()
    val model = DataModel()
    val fence = SdkSceneDeletionFence(3, 2, scene, model, evidence)
    var revocations = 0
    val scope = SdkResourceScope(lease, evidence, fence) { revocations++ }
    init { Handler.reset(); check(evidence.reserve(lease)); evidence.registered(lease) }
    fun seal() { evidence.sealRuntime(11); scope.rendererDetached(); scope.auxiliaryDisposed() }
    fun progress() = evidence.runtimeProgress(11)
    fun pass() = fence.lateExecute(scene)
}

fun main() {
    var count = 0
    fun case(name: String, test: () -> Unit) { test(); count++; println("PASS $name") }
    case("seal forbids a new entity factory") {
        val f = Fixture(); f.seal(); var effects = 0
        check(runCatching { f.scope.createEntity { effects++; Entity(1) } }.exceptionOrNull() is SdkLeaseRevoked)
        check(effects == 0 && f.progress().pendingCallbacks == 0 && f.progress().failures == 0)
    }
    case("seal during allocation keeps the created entity obligation") {
        val f = Fixture()
        val entity = f.scope.createEntity { f.seal(); Entity(1) { f.model.deleted(1) } }
        check(!f.scope.readyToRelease())
        f.scope.destroyEntity(entity); check(!f.scope.readyToRelease())
        f.pass(); check(!f.scope.readyToRelease())
        f.pass(); check(f.scope.readyToRelease())
        f.scope.nativeReleased(); check(f.progress().allRetired)
    }
    case("posted callback admitted before seal drains without running its action") {
        val f = Fixture(); var effects = 0
        f.scope.post { effects++ }
        check(f.progress().pendingCallbacks == 1)
        f.seal(); check(!f.scope.readyToRelease())
        Handler.drain(); check(effects == 0 && f.progress().pendingCallbacks == 0 && f.scope.readyToRelease())
    }
    case("late future after seal is destroyed and needs two cleanup boundaries") {
        val f = Fixture(); val future = CompletableFuture<SceneObject>(); var adopted = 0
        f.scope.sceneObject({ future }, { true }) { _, _ -> adopted++ }
        check(f.progress().pendingCallbacks == 1)
        f.seal(); val obj = SceneObject(); future.complete(obj)
        check(!f.scope.readyToRelease())
        Handler.drain(); check(adopted == 0 && obj.destroys == 1 && f.progress().pendingCallbacks == 0)
        f.pass(); check(!f.scope.readyToRelease())
        f.pass(); check(f.scope.readyToRelease())
    }
    case("queue rejection retains failure but drains the producer") {
        val f = Fixture(); val future = CompletableFuture<SceneObject>()
        f.scope.sceneObject({ future }, { true }) { _, _ -> error("must not adopt") }
        f.seal(); Handler.reject = true; future.complete(SceneObject())
        check(f.progress().pendingCallbacks == 0 && f.progress().failures == 1 && !f.scope.readyToRelease())
        check(f.revocations == 1)
    }
    case("throwing late object disposal cannot become complete") {
        val f = Fixture(); val future = CompletableFuture<SceneObject>()
        f.scope.sceneObject({ future }, { true }) { _, _ -> error("must not adopt") }
        f.seal(); future.complete(SceneObject { error("injected dispose failure") }); Handler.drain()
        f.pass(); f.pass()
        check(f.progress().pendingCallbacks == 0 && f.progress().failures == 1 && !f.scope.readyToRelease())
    }
    case("another Scene cannot advance deletion evidence") {
        val f = Fixture(); val e = f.scope.createEntity { Entity(1) { f.model.deleted(1) } }
        f.seal(); f.scope.destroyEntity(e)
        repeat(3) { f.fence.lateExecute(Scene()) }
        check(!f.scope.readyToRelease()); f.pass(); check(!f.scope.readyToRelease()); f.pass(); check(f.scope.readyToRelease())
    }
    case("closing the captured Scene cannot replace its missing deletion pass") {
        val f = Fixture(); val e = f.scope.createEntity { Entity(1) { f.model.deleted(1) } }
        f.seal(); f.scope.destroyEntity(e); f.fence.close(); repeat(3) { f.pass() }
        check(!f.scope.readyToRelease() && !f.scope.sceneLive() && !f.progress().allRetired)
    }
    case("unexpected delete retracts permission immediately") {
        val f = Fixture(); f.scope.createEntity { Entity(1) }
        f.model.deleted(1)
        check(f.revocations == 1 && f.progress().failures == 1)
        f.seal(); f.pass(); f.pass(); check(!f.scope.readyToRelease())
    }
    case("producer factory failure drains and records failure") {
        val f = Fixture()
        check(runCatching { f.scope.produce<Unit> { error("injected factory failure") } }.isFailure)
        f.seal(); check(f.progress().pendingCallbacks == 0 && f.progress().failures == 1)
    }
    case("late future after Scene close stays failed and never touches old SDK object") {
        val f = Fixture(); val future = CompletableFuture<SceneObject>(); val obj = SceneObject()
        f.scope.sceneObject({ future }, { true }) { _, _ -> error("must not adopt") }
        f.seal(); f.fence.close(); future.complete(obj); Handler.drain()
        check(obj.destroys == 0 && f.progress().pendingCallbacks == 0 && f.progress().failures == 1)
    }
    println("$count boundary cases passed (Android/SDK doubles; no JNI or device execution)")
}
