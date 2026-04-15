// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title UltimateArbitrageExecutor
 * @notice Production-grade multi-protocol flashloan arbitrage executor
 * @dev Supports Balancer V2/V3, Aave V3, Curve flashloans with universal DEX routing
 *
 * KEY FEATURES:
 * - Multi-flashloan provider support (Balancer V2/V3, Aave V3, Curve)
 * - Universal DEX routing (V2: UniswapV2/QuickSwap/Sushi, V3: UniswapV3/Algebra)
 * - Merkle proof verification for route security
 * - Cascade slippage protection
 * - Automatic fee calculation and profit retention
 * - Pool ID + address configuration for specialized DEXs
 * - Emergency rescue functions
 *
 * PROFITABILITY + INSTANT EXECUTION = INSTANT PROFIT
 */

// ============================================================================
// INTERFACES
// ============================================================================

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function decimals() external view returns (uint8);
}

// Balancer V2 Flashloan Interface
interface IBalancerVaultV2 {
    function flashLoan(
        IFlashLoanRecipient recipient,
        address[] calldata tokens,
        uint256[] calldata amounts,
        bytes calldata userData
    ) external;
}

interface IFlashLoanRecipient {
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external;
}

// Balancer V3 Vault Interface (unlock/settle pattern)
interface IBalancerVaultV3 {
    function unlock(bytes calldata data) external returns (bytes memory);
    function settle(address token, uint256 amountHint) external returns (uint256 amountIn);
    function sendTo(address token, address to, uint256 amount) external;
}

// Aave V3 Flashloan Interface
interface IAaveV3Pool {
    function flashLoan(
        address receiverAddress,
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata interestRateModes,
        address onBehalfOf,
        bytes calldata params,
        uint16 referralCode
    ) external;

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IAaveFlashLoanReceiver {
    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

// Curve Flashloan Interface
interface ICurveFlashLoan {
    function flashLoan(
        address receiver,
        address token,
        uint256 amount,
        bytes calldata params
    ) external;
}

// Universal DEX Router Interfaces
interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);

    function getAmountsOut(uint256 amountIn, address[] calldata path)
        external view returns (uint256[] memory amounts);
}

interface IUniswapV3Router {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
}

interface IAlgebraRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 limitSqrtPrice;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
}

interface ICurveRouter {
    function exchange(
        address pool,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        address receiver
    ) external returns (uint256 amountOut);
}

interface IBalancerSwap {
    struct SingleSwap {
        bytes32 poolId;
        uint8 kind;
        address assetIn;
        address assetOut;
        uint256 amount;
        bytes userData;
    }

    struct FundManagement {
        address sender;
        bool fromInternalBalance;
        address payable recipient;
        bool toInternalBalance;
    }

    function swap(
        SingleSwap memory singleSwap,
        FundManagement memory funds,
        uint256 limit,
        uint256 deadline
    ) external returns (uint256);
}

// ============================================================================
// MAIN CONTRACT
// ============================================================================

contract UltimateArbitrageExecutor is IFlashLoanRecipient, IAaveFlashLoanReceiver {

    // ========== CONSTANTS ==========

    uint8 internal constant ROUTE_VERSION_1 = 1;
    uint8 internal constant VAULT_MODE_AUTO = 0;
    uint8 internal constant VAULT_MODE_BALANCER_V2 = 2;
    uint8 internal constant VAULT_MODE_BALANCER_V3 = 3;
    uint8 internal constant VAULT_MODE_AAVE_V3 = 4;
    uint8 internal constant VAULT_MODE_CURVE = 5;

    // Protocol identifiers for route steps
    uint8 internal constant PROTOCOL_UNISWAP_V2 = 1;
    uint8 internal constant PROTOCOL_UNISWAP_V3 = 2;
    uint8 internal constant PROTOCOL_ALGEBRA = 3;
    uint8 internal constant PROTOCOL_CURVE = 4;
    uint8 internal constant PROTOCOL_BALANCER = 5;
    uint8 internal constant PROTOCOL_CUSTOM = 0;

    // Optional step flag
    uint8 internal constant STEP_OPTIONAL_FLAG = 0x80;
    uint8 internal constant STEP_PROTOCOL_MASK = 0x7F;

    // Fee basis points (10000 = 100%)
    uint16 internal constant MAX_FEE_BPS = 10000;
    uint16 internal constant BALANCER_V2_FEE_BPS = 0;
    uint16 internal constant AAVE_V3_FEE_BPS = 9;

    // ========== STORAGE ==========

    address public owner;
    bytes32 public merkleRoot;

    // Flashloan provider addresses
    address public balancerVaultV2;
    address public balancerVaultV3;
    address public aaveV3Pool;
    address public curveFlashLoan;

    // Global vault mode (0 = auto, 2 = force V2, 3 = force V3, 4 = Aave, 5 = Curve)
    uint8 public globalVaultMode;

    // Custom fee for Balancer V3 (if needed)
    uint16 public balancerV3FlashFeeBps;

    // Curve flash fee (basis points)
    uint16 public curveFlashFeeBps;

    // Re-entrancy guard
    bool private _flashActive;

    // Asset-specific vault configurations
    struct AssetVaultConfig {
        address vault;
        uint8 mode;
        bytes32 poolId;
        address poolAddress;
        uint16 customFeeBps;
    }

    mapping(address => AssetVaultConfig) public assetVaultConfig;

    // ========== STRUCTS ==========

    struct RouteStep {
        uint8 protocol;
        address target;
        address approveToken;
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

    struct CallbackContext {
        address asset;
        uint256 amount;
        uint256 minProfit;
        uint256 preLoanBalance;
        address vault;
        uint8 mode;
        bytes32 poolId;
        address poolAddress;
        bytes params;
    }

    // ========== EVENTS ==========

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event MerkleRootUpdated(bytes32 indexed newRoot);
    event GlobalVaultModeUpdated(uint8 indexed mode);
    event BalancerV3FeeUpdated(uint16 indexed feeBps);
    event CurveFeeUpdated(uint16 indexed feeBps);
    event FlashloanProviderUpdated(string indexed provider, address indexed vault);

    event AssetVaultConfigured(
        address indexed asset,
        address indexed vault,
        uint8 indexed mode,
        bytes32 poolId,
        address poolAddress,
        uint16 customFeeBps
    );

    event FlashLoanInitiated(
        address indexed asset,
        uint256 amount,
        uint8 vaultMode,
        bytes32 indexed merkleLeaf,
        uint256 preLoanBalance
    );

    event StepExecuted(
        uint256 indexed stepIndex,
        uint8 protocol,
        address indexed target,
        address indexed approveToken,
        uint256 amountIn,
        uint256 amountOut,
        uint16 feeBps
    );

    event StepSkipped(
        uint256 indexed stepIndex,
        uint8 protocol,
        address indexed target,
        string reason
    );

    event FlashLoanCompleted(
        address indexed asset,
        uint256 amount,
        uint256 flashFee,
        uint256 grossProfit,
        uint256 netProfit,
        address indexed recipient
    );

    event ProfitPaid(
        address indexed token,
        uint256 amount,
        address indexed recipient
    );

    event EmergencyWithdraw(
        address indexed token,
        uint256 amount,
        address indexed recipient
    );

    // ========== ERRORS ==========

    error NotOwner();
    error InvalidAddress();
    error InvalidAmount();
    error InvalidMerkleProof();
    error InvalidCallbackCaller();
    error InvalidLoanAsset();
    error InvalidLoanAmount();
    error InvalidRouteConfig();
    error InvalidVaultMode(uint8 mode);
    error InvalidAssetConfig(address asset);
    error UnknownRouteVersion(uint8 version);
    error RouteExecutionFailed(uint256 step, bytes revertData);
    error StepMinAmountInNotMet(uint256 step, uint256 actual, uint256 required);
    error StepMinAmountOutNotMet(uint256 step, uint256 actual, uint256 required);
    error MinProfitNotMet(uint256 actualProfit, uint256 requiredProfit);
    error ProfitTokenMismatch(address expected, address provided);
    error InsufficientRepayBalance(uint256 balance, uint256 required);
    error InvalidSettlementAmount(uint256 settled, uint256 expected);
    error FlashLoanNotActive();
    error TokenTransferFailed(address token);
    error TokenApproveFailed(address token);
    error BalanceQueryFailed(address token);

    // ========== MODIFIERS ==========

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier flashLoanActive() {
        if (!_flashActive) revert FlashLoanNotActive();
        _;
    }

    // ========== CONSTRUCTOR ==========

    constructor(
        address _balancerVaultV2,
        address _balancerVaultV3,
        address _aaveV3Pool,
        address _curveFlashLoan
    ) {
        if (_balancerVaultV2 == address(0)) revert InvalidAddress();

        owner = msg.sender;
        balancerVaultV2 = _balancerVaultV2;
        balancerVaultV3 = _balancerVaultV3;
        aaveV3Pool = _aaveV3Pool;
        curveFlashLoan = _curveFlashLoan;

        globalVaultMode = VAULT_MODE_AUTO;
        balancerV3FlashFeeBps = 0;
        curveFlashFeeBps = 0;

        emit OwnershipTransferred(address(0), msg.sender);
    }

    receive() external payable {}

    // ========== OWNER FUNCTIONS ==========

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidAddress();
        address oldOwner = owner;
        owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }

    function updateMerkleRoot(bytes32 newRoot) external onlyOwner {
        merkleRoot = newRoot;
        emit MerkleRootUpdated(newRoot);
    }

    function setGlobalVaultMode(uint8 mode) external onlyOwner {
        if (mode > VAULT_MODE_CURVE) revert InvalidVaultMode(mode);
        globalVaultMode = mode;
        emit GlobalVaultModeUpdated(mode);
    }

    function updateFlashloanProvider(
        string calldata providerName,
        address providerAddress
    ) external onlyOwner {
        if (providerAddress == address(0)) revert InvalidAddress();

        bytes32 nameHash = keccak256(bytes(providerName));

        if (nameHash == keccak256("balancer_v2")) {
            balancerVaultV2 = providerAddress;
        } else if (nameHash == keccak256("balancer_v3")) {
            balancerVaultV3 = providerAddress;
        } else if (nameHash == keccak256("aave_v3")) {
            aaveV3Pool = providerAddress;
        } else if (nameHash == keccak256("curve")) {
            curveFlashLoan = providerAddress;
        }

        emit FlashloanProviderUpdated(providerName, providerAddress);
    }

    function setBalancerV3Fee(uint16 feeBps) external onlyOwner {
        if (feeBps > 1000) revert InvalidAmount();
        balancerV3FlashFeeBps = feeBps;
        emit BalancerV3FeeUpdated(feeBps);
    }

    function setCurveFee(uint16 feeBps) external onlyOwner {
        if (feeBps > 1000) revert InvalidAmount();
        curveFlashFeeBps = feeBps;
        emit CurveFeeUpdated(feeBps);
    }

    function configureAssetVault(
        address asset,
        address vault,
        uint8 mode,
        bytes32 poolId,
        address poolAddress,
        uint16 customFeeBps
    ) external onlyOwner {
        if (asset == address(0) || vault == address(0)) revert InvalidAddress();
        if (mode < VAULT_MODE_BALANCER_V2 || mode > VAULT_MODE_CURVE) {
            revert InvalidVaultMode(mode);
        }
        if (customFeeBps > 1000) revert InvalidAmount();

        assetVaultConfig[asset] = AssetVaultConfig({
            vault: vault,
            mode: mode,
            poolId: poolId,
            poolAddress: poolAddress,
            customFeeBps: customFeeBps
        });

        emit AssetVaultConfigured(asset, vault, mode, poolId, poolAddress, customFeeBps);
    }

    function rescueToken(address token) external onlyOwner {
        uint256 balance = _safeBalanceOf(token, address(this));
        if (balance > 0) {
            _safeTransfer(token, owner, balance);
            emit EmergencyWithdraw(token, balance, owner);
        }
    }

    function rescueNative(uint256 amount) external onlyOwner {
        (bool success,) = owner.call{value: amount}("");
        if (!success) revert TokenTransferFailed(address(0));
    }

    // ========== MAIN FLASHLOAN ENTRY POINT ==========

    function executeArbitrage(
        address asset,
        uint256 amount,
        uint256 minProfit,
        bytes32[] calldata proof,
        bytes calldata params
    ) external onlyOwner {
        if (asset == address(0)) revert InvalidAddress();
        if (amount == 0) revert InvalidAmount();

        bytes32 leaf = keccak256(params);
        if (!_verifyMerkleProof(leaf, proof)) revert InvalidMerkleProof();

        uint256 preLoanBalance = _safeBalanceOf(asset, address(this));

        AssetVaultConfig memory config = assetVaultConfig[asset];
        address selectedVault;
        uint8 selectedMode;
        bytes32 selectedPoolId;
        address selectedPoolAddress;

        if (globalVaultMode != VAULT_MODE_AUTO) {
            selectedMode = globalVaultMode;
            selectedVault = _getVaultForMode(selectedMode);
            selectedPoolId = bytes32(0);
            selectedPoolAddress = address(0);
        } else if (config.vault != address(0)) {
            selectedVault = config.vault;
            selectedMode = config.mode;
            selectedPoolId = config.poolId;
            selectedPoolAddress = config.poolAddress;
        } else {
            selectedVault = balancerVaultV2;
            selectedMode = VAULT_MODE_BALANCER_V2;
            selectedPoolId = bytes32(0);
            selectedPoolAddress = address(0);
        }

        if (selectedVault == address(0)) revert InvalidAssetConfig(asset);

        CallbackContext memory ctx = CallbackContext({
            asset: asset,
            amount: amount,
            minProfit: minProfit,
            preLoanBalance: preLoanBalance,
            vault: selectedVault,
            mode: selectedMode,
            poolId: selectedPoolId,
            poolAddress: selectedPoolAddress,
            params: params
        });

        _flashActive = true;

        emit FlashLoanInitiated(asset, amount, selectedMode, leaf, preLoanBalance);

        if (selectedMode == VAULT_MODE_BALANCER_V3) {
            _executeBalancerV3Flash(ctx);
        } else if (selectedMode == VAULT_MODE_BALANCER_V2) {
            _executeBalancerV2Flash(ctx);
        } else if (selectedMode == VAULT_MODE_AAVE_V3) {
            _executeAaveV3Flash(ctx);
        } else if (selectedMode == VAULT_MODE_CURVE) {
            _executeCurveFlash(ctx);
        } else {
            revert InvalidVaultMode(selectedMode);
        }

        _flashActive = false;
    }

    // ========== FLASHLOAN EXECUTION METHODS ==========

    function _executeBalancerV2Flash(CallbackContext memory ctx) internal {
        address[] memory tokens = new address[](1);
        uint256[] memory amounts = new uint256[](1);
        tokens[0] = ctx.asset;
        amounts[0] = ctx.amount;

        IBalancerVaultV2(ctx.vault).flashLoan(
            IFlashLoanRecipient(address(this)),
            tokens,
            amounts,
            abi.encode(ctx)
        );
    }

    function _executeBalancerV3Flash(CallbackContext memory ctx) internal {
        IBalancerVaultV3(ctx.vault).unlock(abi.encode(ctx));
    }

    function _executeAaveV3Flash(CallbackContext memory ctx) internal {
        address[] memory assets = new address[](1);
        uint256[] memory amounts = new uint256[](1);
        uint256[] memory modes = new uint256[](1);

        assets[0] = ctx.asset;
        amounts[0] = ctx.amount;
        modes[0] = 0;

        IAaveV3Pool(ctx.vault).flashLoan(
            address(this),
            assets,
            amounts,
            modes,
            address(this),
            abi.encode(ctx),
            0
        );
    }

    function _executeCurveFlash(CallbackContext memory ctx) internal {
        ICurveFlashLoan(ctx.vault).flashLoan(
            address(this),
            ctx.asset,
            ctx.amount,
            abi.encode(ctx)
        );
    }

    // ========== FLASHLOAN CALLBACKS ==========

    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external override flashLoanActive {
        if (tokens.length != 1 || amounts.length != 1) revert InvalidLoanAmount();

        CallbackContext memory ctx = abi.decode(userData, (CallbackContext));

        if (msg.sender != ctx.vault) revert InvalidCallbackCaller();
        if (ctx.mode != VAULT_MODE_BALANCER_V2) revert InvalidVaultMode(ctx.mode);
        if (tokens[0] != ctx.asset) revert InvalidLoanAsset();
        if (amounts[0] != ctx.amount) revert InvalidLoanAmount();

        uint256 flashFee = feeAmounts[0];
        _executeRouteAndSettle(ctx, flashFee, false);
    }

    function unlockCallback(bytes calldata data) external flashLoanActive returns (bytes memory) {
        CallbackContext memory ctx = abi.decode(data, (CallbackContext));

        if (msg.sender != ctx.vault) revert InvalidCallbackCaller();
        if (ctx.mode != VAULT_MODE_BALANCER_V3) revert InvalidVaultMode(ctx.mode);

        IBalancerVaultV3(ctx.vault).sendTo(ctx.asset, address(this), ctx.amount);

        uint256 flashFee = _applyBps(ctx.amount, balancerV3FlashFeeBps);

        (uint256 grossProfit, uint256 netProfit) = _executeRouteAndSettle(ctx, flashFee, true);

        return abi.encode(grossProfit, netProfit);
    }

    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external override flashLoanActive returns (bool) {
        if (assets.length != 1 || amounts.length != 1) revert InvalidLoanAmount();

        CallbackContext memory ctx = abi.decode(params, (CallbackContext));

        if (msg.sender != ctx.vault) revert InvalidCallbackCaller();
        if (ctx.mode != VAULT_MODE_AAVE_V3) revert InvalidVaultMode(ctx.mode);
        if (initiator != address(this)) revert InvalidCallbackCaller();
        if (assets[0] != ctx.asset) revert InvalidLoanAsset();
        if (amounts[0] != ctx.amount) revert InvalidLoanAmount();

        uint256 flashFee = premiums[0];
        _executeRouteAndSettle(ctx, flashFee, false);

        uint256 totalDebt = ctx.amount + flashFee;
        _forceApprove(ctx.asset, ctx.vault, totalDebt);

        return true;
    }

    // ========== ROUTE EXECUTION & SETTLEMENT ==========

    function _executeRouteAndSettle(
        CallbackContext memory ctx,
        uint256 flashFee,
        bool useV3Settle
    ) internal returns (uint256 grossProfit, uint256 netProfit) {
        uint256 balanceWithLoan = _safeBalanceOf(ctx.asset, address(this));
        if (balanceWithLoan < ctx.preLoanBalance + ctx.amount) {
            revert InvalidLoanAmount();
        }

        (
            RouteStep[] memory steps,
            address profitToken,
            uint256 gasReserve,
            uint256 dexFeeReserve
        ) = _decodeRoute(ctx.params);

        if (profitToken != address(0) && profitToken != ctx.asset) {
            revert ProfitTokenMismatch(ctx.asset, profitToken);
        }

        uint256 cascadeMinIn = 0;

        for (uint256 i = 0; i < steps.length; i++) {
            RouteStep memory step = steps[i];

            if (step.target == address(0)) revert InvalidRouteConfig();

            bool isOptional = (step.protocol & STEP_OPTIONAL_FLAG) != 0;
            uint8 normalizedProtocol = step.protocol & STEP_PROTOCOL_MASK;

            uint256 balanceBefore = _safeBalanceOf(ctx.asset, address(this));

            uint256 requiredMinIn = step.minAmountIn > cascadeMinIn ?
                step.minAmountIn : cascadeMinIn;

            if (requiredMinIn > 0 && balanceBefore < requiredMinIn) {
                if (isOptional) {
                    emit StepSkipped(i, normalizedProtocol, step.target, "minAmountIn");
                    continue;
                }
                revert StepMinAmountInNotMet(i, balanceBefore, requiredMinIn);
            }

            uint256 approvalAmount = 0;
            if (step.approveToken != address(0)) {
                approvalAmount = _safeBalanceOf(step.approveToken, address(this));
                if (approvalAmount > 0) {
                    _forceApprove(step.approveToken, step.target, approvalAmount);
                }
            }

            (bool success, bytes memory returnData) = step.target.call{value: step.callValue}(step.data);

            if (step.approveToken != address(0) && approvalAmount > 0) {
                _forceApprove(step.approveToken, step.target, 0);
            }

            if (!success) {
                if (isOptional) {
                    emit StepSkipped(i, normalizedProtocol, step.target, "execution_failed");
                    continue;
                }
                revert RouteExecutionFailed(i, returnData);
            }

            uint256 balanceAfter = _safeBalanceOf(ctx.asset, address(this));
            uint256 actualOut = balanceAfter > balanceBefore ?
                balanceAfter - balanceBefore : 0;

            if (step.minAmountOut > 0 && actualOut < step.minAmountOut) {
                if (isOptional) {
                    emit StepSkipped(i, normalizedProtocol, step.target, "minAmountOut");
                    continue;
                }
                revert StepMinAmountOutNotMet(i, actualOut, step.minAmountOut);
            }

            if (actualOut > 0 && step.feeBps > 0) {
                cascadeMinIn = _applyBps(actualOut, MAX_FEE_BPS - step.feeBps);
            }

            emit StepExecuted(
                i,
                normalizedProtocol,
                step.target,
                step.approveToken,
                balanceBefore,
                actualOut,
                step.feeBps
            );
        }

        uint256 totalRepayment = ctx.amount + flashFee;
        uint256 currentBalance = _safeBalanceOf(ctx.asset, address(this));

        if (currentBalance < totalRepayment) {
            revert InsufficientRepayBalance(currentBalance, totalRepayment);
        }

        if (useV3Settle) {
            _safeTransfer(ctx.asset, ctx.vault, totalRepayment);
            uint256 settled = IBalancerVaultV3(ctx.vault).settle(ctx.asset, totalRepayment);

            if (settled < ctx.amount || settled > totalRepayment) {
                revert InvalidSettlementAmount(settled, totalRepayment);
            }
        } else {
            _safeTransfer(ctx.asset, ctx.vault, totalRepayment);
        }

        uint256 balanceAfterRepay = _safeBalanceOf(ctx.asset, address(this));
        grossProfit = balanceAfterRepay > ctx.preLoanBalance ?
            balanceAfterRepay - ctx.preLoanBalance : 0;

        uint256 requiredProfit = ctx.minProfit + gasReserve + dexFeeReserve;
        if (grossProfit < requiredProfit) {
            revert MinProfitNotMet(grossProfit, requiredProfit);
        }

        netProfit = grossProfit - gasReserve - dexFeeReserve;

        if (netProfit > 0) {
            _safeTransfer(ctx.asset, owner, netProfit);
            emit ProfitPaid(ctx.asset, netProfit, owner);
        }

        emit FlashLoanCompleted(
            ctx.asset,
            ctx.amount,
            flashFee,
            grossProfit,
            netProfit,
            owner
        );
    }

    // ========== HELPER FUNCTIONS ==========

    function _getVaultForMode(uint8 mode) internal view returns (address) {
        if (mode == VAULT_MODE_BALANCER_V2) return balancerVaultV2;
        if (mode == VAULT_MODE_BALANCER_V3) return balancerVaultV3;
        if (mode == VAULT_MODE_AAVE_V3) return aaveV3Pool;
        if (mode == VAULT_MODE_CURVE) return curveFlashLoan;
        return address(0);
    }

    function _decodeRoute(bytes memory params)
        internal
        pure
        returns (
            RouteStep[] memory steps,
            address profitToken,
            uint256 gasReserve,
            uint256 dexFeeReserve
        )
    {
        if (params.length == 0) {
            return (new RouteStep[](0), address(0), 0, 0);
        }

        uint256 firstWord = _firstWord(params);
        if (firstWord <= 32) {
            RouteEnvelope memory envelope = abi.decode(params, (RouteEnvelope));
            if (envelope.version != ROUTE_VERSION_1) {
                revert UnknownRouteVersion(envelope.version);
            }
            return (
                envelope.steps,
                envelope.profitToken,
                envelope.gasReserveAsset,
                envelope.dexFeeReserveAsset
            );
        }

        (
            address[] memory targets,
            address[] memory approveTokens,
            bytes[] memory datas
        ) = abi.decode(params, (address[], address[], bytes[]));

        if (targets.length == 0 ||
            targets.length != approveTokens.length ||
            targets.length != datas.length) {
            revert InvalidRouteConfig();
        }

        steps = new RouteStep[](targets.length);
        for (uint256 i = 0; i < targets.length; i++) {
            steps[i] = RouteStep({
                protocol: PROTOCOL_CUSTOM,
                target: targets[i],
                approveToken: approveTokens[i],
                callValue: 0,
                minAmountIn: 0,
                minAmountOut: 0,
                feeBps: 0,
                data: datas[i]
            });
        }

        return (steps, address(0), 0, 0);
    }

    function _verifyMerkleProof(bytes32 leaf, bytes32[] calldata proof)
        internal
        view
        returns (bool)
    {
        bytes32 computed = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 proofElement = proof[i];
            if (computed <= proofElement) {
                computed = keccak256(abi.encodePacked(computed, proofElement));
            } else {
                computed = keccak256(abi.encodePacked(proofElement, computed));
            }
        }
        return computed == merkleRoot;
    }

    function _forceApprove(address token, address spender, uint256 amount) internal {
        _safeApprove(token, spender, 0);
        if (amount > 0) {
            _safeApprove(token, spender, amount);
        }
    }

    function _safeTransfer(address token, address to, uint256 amount) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(IERC20.transfer.selector, to, amount)
        );
        if (!success || (data.length > 0 && !abi.decode(data, (bool)))) {
            revert TokenTransferFailed(token);
        }
    }

    function _safeApprove(address token, address spender, uint256 amount) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(IERC20.approve.selector, spender, amount)
        );
        if (!success || (data.length > 0 && !abi.decode(data, (bool)))) {
            revert TokenApproveFailed(token);
        }
    }

    function _safeBalanceOf(address token, address account) internal view returns (uint256) {
        (bool success, bytes memory data) = token.staticcall(
            abi.encodeWithSelector(IERC20.balanceOf.selector, account)
        );
        if (!success || data.length < 32) {
            revert BalanceQueryFailed(token);
        }
        return abi.decode(data, (uint256));
    }

    function _applyBps(uint256 amount, uint256 bps) internal pure returns (uint256) {
        if (bps > MAX_FEE_BPS) bps = MAX_FEE_BPS;
        return (amount * bps) / MAX_FEE_BPS;
    }

    function _firstWord(bytes memory data) internal pure returns (uint256 word) {
        assembly {
            word := mload(add(data, 0x20))
        }
    }
}
