// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "https://github.com/OpenZeppelin/openzeppelin-contracts/blob/v4.9.6/contracts/token/ERC20/IERC20.sol";
import "https://github.com/OpenZeppelin/openzeppelin-contracts/blob/v4.9.6/contracts/token/ERC20/utils/SafeERC20.sol";

interface IAaveV3Pool {
    function flashLoanSimple(address receiver, address asset, uint256 amount, bytes calldata params, uint16 ref) external;
}

interface IBalancerV3Vault {
    function unlock(bytes calldata data) external returns (bytes memory);
    function sendTo(IERC20 token, address to, uint256 amount) external;
    function settle(IERC20 token, uint256 amountHint) external returns (uint256);
}

contract InstitutionalExecutor {
    using SafeERC20 for IERC20;

    struct RouteStep {
        uint8 protocol;
        address target;
        address approveToken;
        address outputToken;
        uint256 callValue;
        uint256 minAmountIn;
        uint256 minAmountOut;
        uint16 feeBps;
        bytes data;
    }

    struct RouteEnvelope {
        uint8 version;
        address profitToken;
        uint256 gasReserveAsset;
        uint256 dexFeeReserveAsset;
        RouteStep[] steps;
    }

    address public immutable owner;
    IAaveV3Pool public constant AAVE_POOL = IAaveV3Pool(0x794a61358D6845594F94dc1DB02A252b5b4814aD);
    IBalancerV3Vault public constant BALANCER_VAULT = IBalancerV3Vault(0xbA1333333333a1BA1108E8412f11850A5C319bA9);
    uint256 public constant MAX_ROUTE_STEPS = 24;
    uint256 public constant ONE_BPS = 1;
    uint256 public constant BPS_DENOMINATOR = 10_000;

    uint256 private _callbackLock;

    error Unauthorized();
    error ArbFailed();
    error InvalidCallback();
    error InvalidPayload();
    error InvalidLegacyPayload();
    error InvalidRouteEnvelope();
    error RouteStepFailed(uint256 index, bytes reason);
    error RouteStepOutputTooLow(uint256 index, uint256 actual, uint256 required);
    error RouteReserveUnderrun(uint256 actual, uint256 required);
    error InsufficientProfit(uint256 actual, uint256 required);

    event FlashArbExecuted(address indexed asset, uint256 amount, uint256 profit);
    event RouteStepExecuted(uint256 indexed index, address indexed target, uint256 callValue);
    event RouteEnvelopeExecuted(uint8 version, uint256 steps, address indexed profitToken);

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    modifier callbackGuard() {
        if (_callbackLock != 1) revert InvalidCallback();
        _callbackLock = 2;
        _;
        _callbackLock = 0;
    }

    constructor() {
        owner = msg.sender;
    }

    function initAaveFlash(address asset, uint256 amount, uint256 minProfit, bytes calldata payload) external onlyOwner {
        _callbackLock = 1;
        AAVE_POOL.flashLoanSimple(address(this), asset, amount, abi.encode(minProfit, payload), 0);
    }

    function initBalancerFlash(address asset, uint256 amount, uint256 minProfit, bytes calldata payload) external onlyOwner {
        _callbackLock = 1;
        BALANCER_VAULT.unlock(abi.encode(asset, amount, minProfit, payload));
    }

    function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params) external callbackGuard returns (bool) {
        if (msg.sender != address(AAVE_POOL) || initiator != address(this)) revert InvalidCallback();
        (uint256 minProfit, bytes memory payload) = abi.decode(params, (uint256, bytes));

        _executeLogic(asset, payload);

        uint256 owed = amount + premium;
        uint256 bal = IERC20(asset).balanceOf(address(this));
        if (bal < owed + minProfit) revert InsufficientProfit(bal >= owed ? bal - owed : 0, minProfit);

        IERC20(asset).safeApprove(address(AAVE_POOL), owed);
        return true;
    }

    function unlockCallback(bytes calldata data) external callbackGuard returns (bytes memory) {
        if (msg.sender != address(BALANCER_VAULT)) revert InvalidCallback();
        (address asset, uint256 amount, uint256 minProfit, bytes memory payload) = abi.decode(data, (address, uint256, uint256, bytes));

        BALANCER_VAULT.sendTo(IERC20(asset), address(this), amount);
        _executeLogic(asset, payload);

        uint256 bal = IERC20(asset).balanceOf(address(this));
        if (bal < amount + minProfit) revert InsufficientProfit(bal >= amount ? bal - amount : 0, minProfit);

        IERC20(asset).safeTransfer(address(BALANCER_VAULT), amount);
        BALANCER_VAULT.settle(IERC20(asset), amount);
        return "";
    }

    function _executeLogic(address asset, bytes memory payload) internal {
        (bool hasEnvelope, RouteEnvelope memory route) = _tryDecodeEnvelope(payload);
        if (hasEnvelope) {
            _executeEnvelope(asset, route);
            return;
        }
        _executeLegacy(asset, payload);
    }

    function _executeLegacy(address asset, bytes memory payload) internal {
        try this.decodeLegacyPayload(payload) returns (
            address[] memory targets,
            uint256[] memory transferAmounts,
            bytes[] memory callDatas
        ) {
            if (targets.length == 0 || targets.length != transferAmounts.length || targets.length != callDatas.length) {
                revert InvalidLegacyPayload();
            }

            for (uint256 i = 0; i < targets.length; i++) {
                if (targets[i] == address(0)) revert InvalidLegacyPayload();
                if (transferAmounts[i] > 0) {
                    IERC20(asset).safeTransfer(targets[i], transferAmounts[i]);
                }

                (bool success, bytes memory ret) = targets[i].call(callDatas[i]);
                if (!success) revert RouteStepFailed(i, ret);
            }
        } catch {
            revert InvalidPayload();
        }
    }

    function _executeEnvelope(address asset, RouteEnvelope memory route) internal {
        if (!_isValidEnvelope(route)) revert InvalidRouteEnvelope();

        for (uint256 i = 0; i < route.steps.length; i++) {
            RouteStep memory step = route.steps[i];
            if (step.target == address(0)) revert InvalidRouteEnvelope();

            address tokenIn = step.approveToken == address(0) ? asset : step.approveToken;
            if (tokenIn != address(0) && step.minAmountIn > 0) {
                _forceApprove(IERC20(tokenIn), step.target, step.minAmountIn);
            }

            uint256 outBefore = 0;
            if (step.outputToken != address(0) && step.minAmountOut > 0) {
                outBefore = IERC20(step.outputToken).balanceOf(address(this));
            }

            (bool success, bytes memory ret) = step.target.call{value: step.callValue}(step.data);
            if (!success) revert RouteStepFailed(i, ret);
            emit RouteStepExecuted(i, step.target, step.callValue);

            if (step.outputToken != address(0) && step.minAmountOut > 0) {
                uint256 outAfter = IERC20(step.outputToken).balanceOf(address(this));
                uint256 delta = outAfter > outBefore ? outAfter - outBefore : 0;
                if (delta < step.minAmountOut) {
                    revert RouteStepOutputTooLow(i, delta, step.minAmountOut);
                }
            }
        }

        uint256 reserveNeeded = route.gasReserveAsset + route.dexFeeReserveAsset;
        if (reserveNeeded > 0 && route.profitToken != address(0)) {
            uint256 bal = IERC20(route.profitToken).balanceOf(address(this));
            if (bal < reserveNeeded) revert RouteReserveUnderrun(bal, reserveNeeded);
        }

        emit RouteEnvelopeExecuted(route.version, route.steps.length, route.profitToken);
    }

    function _forceApprove(IERC20 token, address spender, uint256 amount) internal {
        token.safeApprove(spender, 0);
        token.safeApprove(spender, amount);
    }

    function _applyBps(uint256 amount, uint256 bps) internal pure returns (uint256) {
        return (amount * bps * ONE_BPS) / BPS_DENOMINATOR;
    }

    function _isValidEnvelope(RouteEnvelope memory route) internal pure returns (bool) {
        if (route.version == 0) return false;
        if (route.steps.length == 0 || route.steps.length > MAX_ROUTE_STEPS) return false;
        return true;
    }

    function _tryDecodeEnvelope(bytes memory payload) internal returns (bool, RouteEnvelope memory) {
        try this.decodeRouteEnvelope(payload) returns (RouteEnvelope memory route) {
            if (_isValidEnvelope(route)) {
                return (true, route);
            }
            return (false, route);
        } catch {
            RouteStep[] memory emptySteps = new RouteStep[](0);
            return (false, RouteEnvelope({
                version: 0,
                profitToken: address(0),
                gasReserveAsset: 0,
                dexFeeReserveAsset: 0,
                steps: emptySteps
            }));
        }
    }

    function decodeRouteEnvelope(bytes calldata payload) external pure returns (RouteEnvelope memory route) {
        route = abi.decode(payload, (RouteEnvelope));
    }

    function decodeLegacyPayload(bytes calldata payload)
        external
        pure
        returns (address[] memory targets, uint256[] memory transferAmounts, bytes[] memory callDatas)
    {
        (targets, transferAmounts, callDatas) = abi.decode(payload, (address[], uint256[], bytes[]));
    }

    function approveRouter(address token, address router) external onlyOwner {
        IERC20(token).safeApprove(router, type(uint256).max);
    }

    function rescueToken(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        if (bal > 0) IERC20(token).safeTransfer(owner, bal);
    }

    receive() external payable {}
}
